"""GPU 리랭킹 서비스 — FastAPI.

AWS 앱의 ``RemoteRerankerClient``가 이 서비스를 부른다. 계약은 둘뿐이다:

- ``GET  /health``       — 인증 없음. 모델 적재 여부와 **실제 실행 공급자**를 돌려준다.
- ``POST /v1/rerank``    — Bearer 토큰. ``{query, passages}`` → ``{scores}``.

응답 ``scores``는 입력 ``passages`` 순서와 1:1로 맞아야 한다 — 앱이
``zip(hits, scores, strict=True)``로 묶기 때문에 개수가 어긋나면 검색 한복판에서
터진다. 앱 쪽에도 검증이 있지만 여기서 먼저 지킨다.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from gpu_service.app.config import ServiceConfig
from gpu_service.app.embed_service import EmbedService
from gpu_service.app.gpu_queue import GpuBusy, GpuQueue
from gpu_service.app.parse_service import ClientGone, ParseService, ParseTimeout
from gpu_service.app.service import RerankService
from gpu_service.app.stats_history import SAMPLE_INTERVAL_S, StatsHistory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("gpu_service")

config = ServiceConfig.from_env()
# 리랭커와 임베딩이 **같은 카드**를 쓴다. 큐를 하나만 두고 둘이 나눠 갖는다 -
# 각자 하나씩 가지면 리허설과 색인이 겹쳐 돌아 VRAM이 두 배로 필요해진다.
_gpu_queue = GpuQueue(config.max_concurrency, config.max_wait_s)
service = RerankService(config, queue=_gpu_queue)
embed_service = EmbedService(config, queue=_gpu_queue) if config.embed_enabled else None
# 파싱은 **전용 레인**이다. 공유 큐에 넣으면 수 분짜리 변환이 1.5초 리랭킹을 막고
# EWMA를 오염시켜(200초 1건에 avg 4.5→44초) 리랭킹이 허위 429를 맞는다.
# initial_task_s=60: 파싱 규모에 맞는 시드 - 4.5초에서 시작하면 첫 몇 건의
# 예상 대기가 크게 과소평가된다.
_parse_lane = GpuQueue(1, config.parse_max_wait_s, initial_task_s=60.0)
parse_service = ParseService(config, lane=_parse_lane) if config.parse_enabled else None
_history = StatsHistory()
# 누적 카운터(completed/rejected)의 기준점. "누적 N회"는 이 시각부터라는 것을
# 대시보드가 보여줄 수 있어야 한 달 뒤에도 숫자가 해석된다.
_started_at = time.time()


async def _sample_loop() -> None:
    """5초마다 큐·GPU 상태를 이력에 남긴다 — 대시보드의 최근 1시간 그래프 원천."""
    while True:
        try:
            _history.record(t=time.time(), queue=_gpu_queue.snapshot(), gpu=gpu_metrics())
        except Exception:  # noqa: BLE001 — 샘플 한 번 실패로 루프가 죽으면 이력이 통째로 끊긴다
            logger.exception("stats sample failed")
        await asyncio.sleep(SAMPLE_INTERVAL_S)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    passages: list[str] = Field(min_length=1)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)


class EmbedResponse(BaseModel):
    # 순서는 입력 texts와 1:1이다. 호출부가 그 전제로 청크와 벡터를 묶으므로
    # 어긋나면 색인이 조용히 뒤섞인다.
    vectors: list[list[float]]
    dimension: int
    device: str
    elapsed_ms: float


class RerankResponse(BaseModel):
    scores: list[float]
    device: str
    elapsed_ms: float


def require_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """공유 시크릿 확인 — 타이밍 비교로 토큰을 알아내지 못하게 compare_digest."""
    if config.allow_anon and not config.token:
        return
    expected = f"Bearer {config.token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config.validate()
    # 뜰 때 적재한다. 첫 요청에 미루면 그 요청이 모델 로드까지 뒤집어써서
    # 앱 타임아웃(60초)에 걸리고, 실패해도 컨테이너가 살아 있어 알아채기 어렵다.
    service.load()
    if embed_service is not None:
        embed_service.load()
    if parse_service is not None:
        parse_service.load()
    sampler = asyncio.create_task(_sample_loop())
    try:
        yield
    finally:
        sampler.cancel()
        with suppress(asyncio.CancelledError):
            await sampler



def gpu_metrics() -> dict[str, object] | None:
    """현재 GPU 상태 — 읽을 수 없으면 None.

    Netdata 같은 GPU 대시보드가 있어도 이걸 /health에 두는 이유: **조용한 CPU 폴백은
    GPU 지표만으로는 안 잡힌다.** onnxruntime 버전이 어긋나 CPU로 떨어지면 GPU는
    그냥 한가한 걸로 보인다(2026-08-20 실제로 겪었다). ``on_gpu``와 사용률이 한
    화면에 같이 있어야 "안 쓰는 중"과 "못 쓰는 중"이 구분된다.
    """
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            name = pynvml.nvmlDeviceGetName(h)
            return {
                "name": name.decode() if isinstance(name, bytes) else name,
                "utilization_pct": util.gpu,
                "memory_used_mib": mem.used // 1024 // 1024,
                "memory_total_mib": mem.total // 1024 // 1024,
                "temperature_c": pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU),
                "power_w": round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1),
            }
        finally:
            pynvml.nvmlShutdown()
    except Exception:  # noqa: BLE001 — 지표를 못 읽는다고 /health가 죽으면 안 된다
        return None


app = FastAPI(title="rown GPU reranker", lifespan=lifespan)


@app.exception_handler(GpuBusy)
async def _gpu_busy_handler(request: Request, exc: GpuBusy) -> JSONResponse:
    """예상 대기가 클라이언트 타임아웃을 넘을 때만 429.

    큐가 길다는 이유만으로 거절하지 않는다 - 기다렸다 GPU로 처리하는 편이 CPU
    폴백보다 거의 항상 빠르기 때문이다. 여기 오는 요청은 기다려 봐야 타임아웃 후
    폴백이라 총 시간이 더 나빠지는 경우뿐이다.
    """
    logger.warning(
        "gpu busy: in_flight=%d 예상대기=%.1fs 상한=%.0fs",
        exc.in_flight, exc.estimated_wait_s, exc.limit_s,
    )
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "detail": str(exc),
            "in_flight": exc.in_flight,
            "estimated_wait_s": round(exc.estimated_wait_s, 1),
        },
        headers={"Retry-After": "5"},
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if service.ready else "loading",
        "started_at": int(_started_at),
        "ready": service.ready,
        "device_requested": config.device,
        "on_gpu": service.on_gpu,
        "providers": service.providers,
        "model_dir": config.model_dir,
        "batch_size": config.batch_size,
        "max_length": config.max_length,
        "warmup_ms": service.warmup_ms,
        # 큐가 밀리는지 밖에서 보이게 한다. rejected_total이 0이 아니면 용량을
        # 다시 봐야 한다는 신호다.
        "queue": _gpu_queue.snapshot(),
        "gpu": gpu_metrics(),
        # 임베딩은 선택적으로 켜진다. 꺼져 있으면 embed=None이라 호출부가
        # "느린데 왜지"를 여기서 바로 구분할 수 있다.
        "embed": None
        if embed_service is None
        else {
            "ready": embed_service.ready,
            "on_gpu": embed_service.on_gpu,
            "model_dir": config.embed_model_dir,
            "dimension": embed_service.dimension,
            "max_chars_per_batch": embed_service.max_chars,
            "warmup_ms": embed_service.warmup_ms,
        },
        # 파싱도 선택적이다. lane 스냅샷을 따로 내는 이유: 파싱은 전용 레인이라
        # 위의 queue(리랭킹·임베딩)와 통계가 섞이지 않는다.
        "parse": None
        if parse_service is None
        else {
            "ready": parse_service.ready,
            "device": parse_service.device,
            "docling_version": parse_service.docling_version,
            "artifacts_dir": config.parse_artifacts_dir,
            "lane": _parse_lane.snapshot(),
        },
    }


@app.get("/stats/history")
async def stats_history() -> dict[str, object]:
    """최근 1시간의 큐·GPU 시계열 — 앱 대시보드가 그래프를 그리는 원천.

    /health처럼 인증이 없다. 양쪽 다 루프백에만 묶인 터널로만 닿을 수 있고,
    수치에는 비밀이 없다.
    """
    return _history.series()


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """Prometheus 텍스트 — netdata go.d의 prometheus 수집기가 긁는다.

    하드웨어 수치(사용률·VRAM·온도)는 여기 없다 - netdata의 nvidia_smi 수집기가
    이미 뜬다. 여기 두는 것은 **서비스만 아는 것**이다: 큐가 얼마나 밀렸는지,
    그리고 모델이 정말 GPU에 있는지(``on_gpu``). onnxruntime이 어긋나 CPU로
    떨어지면 GPU 지표는 그저 한가해 보인다 - 이 플래그가 있어야 알림을 걸 수 있다.
    """
    q = _gpu_queue.snapshot()
    lines = [
        "# TYPE rown_gpu_queue_in_flight gauge",
        f"rown_gpu_queue_in_flight {q['in_flight']}",
        "# TYPE rown_gpu_queue_estimated_wait_seconds gauge",
        f"rown_gpu_queue_estimated_wait_seconds {q['estimated_wait_s']}",
        "# TYPE rown_gpu_queue_avg_task_seconds gauge",
        f"rown_gpu_queue_avg_task_seconds {q['avg_task_s']}",
        "# TYPE rown_gpu_queue_completed_total counter",
        f"rown_gpu_queue_completed_total {q['completed_total']}",
        "# TYPE rown_gpu_queue_rejected_total counter",
        f"rown_gpu_queue_rejected_total {q['rejected_total']}",
        "# TYPE rown_gpu_service_ready gauge",
        f'rown_gpu_service_ready{{service="rerank"}} {int(service.ready)}',
        "# TYPE rown_gpu_service_on_gpu gauge",
        f'rown_gpu_service_on_gpu{{service="rerank"}} {int(service.on_gpu)}',
    ]
    if embed_service is not None:
        lines += [
            f'rown_gpu_service_ready{{service="embed"}} {int(embed_service.ready)}',
            f'rown_gpu_service_on_gpu{{service="embed"}} {int(embed_service.on_gpu)}',
        ]
    if parse_service is not None:
        p = _parse_lane.snapshot()
        lines += [
            f'rown_gpu_service_ready{{service="parse"}} {int(parse_service.ready)}',
            "# TYPE rown_gpu_parse_in_flight gauge",
            f"rown_gpu_parse_in_flight {p['in_flight']}",
            "# TYPE rown_gpu_parse_completed_total counter",
            f"rown_gpu_parse_completed_total {p['completed_total']}",
            "# TYPE rown_gpu_parse_rejected_total counter",
            f"rown_gpu_parse_rejected_total {p['rejected_total']}",
        ]
    return "\n".join(lines) + "\n"


@app.post("/v1/rerank", response_model=RerankResponse)
async def rerank(
    request: RerankRequest,
    _: Annotated[None, Depends(require_token)] = None,
) -> RerankResponse:
    if len(request.passages) > config.max_passages:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"passages {len(request.passages)} > 상한 {config.max_passages}",
        )
    if not service.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="model not loaded"
        )

    t0 = time.perf_counter()
    scores = await service.score(request.query, request.passages)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "rerank n=%d elapsed_ms=%s gpu=%s", len(request.passages), elapsed_ms, service.on_gpu
    )
    return RerankResponse(
        scores=scores,
        device="cuda" if service.on_gpu else "cpu",
        elapsed_ms=elapsed_ms,
    )


@app.post("/v1/parse")
async def parse(
    request: Request,
    file: Annotated[UploadFile, File()],
    page_break_placeholder: Annotated[str, Form()] = "",
    _: Annotated[None, Depends(require_token)] = None,
) -> dict[str, object]:
    """PDF → 마크다운. 페이지 마커 문자열은 클라이언트가 보낸다(계약은 앱 소유).

    multipart인 이유: 50MB PDF를 base64 JSON으로 싸면 67MB가 된다. 스트리밍으로
    임시 파일에 쓰면서 바이트를 직접 세는 이유: Content-Length는 클라이언트
    주장일 뿐이라 믿으면 상한을 우회당한다.
    """
    if parse_service is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="파싱이 꺼져 있습니다 (GPU_PARSE_ARTIFACTS_DIR 미설정)",
        )
    if not parse_service.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="parse converter not loaded"
        )

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = Path(tmp.name)
    received = 0
    try:
        try:
            while chunk := await file.read(1024 * 1024):
                received += len(chunk)
                if received > config.parse_max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"파일이 상한 {config.parse_max_bytes}바이트를 넘습니다",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()
        if received == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="빈 파일")

        try:
            return await parse_service.parse(
                tmp_path, page_break_placeholder, request.is_disconnected
            )
        except ParseTimeout as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)
            ) from exc
        except ClientGone:
            # 받을 사람이 없다 - 이 응답은 어차피 버려지므로 코드는 로그용이다.
            logger.info("parse client gone: bytes=%d", received)
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="client gone"
            ) from None
    finally:
        with suppress(OSError):
            tmp_path.unlink()


@app.post("/v1/embed", response_model=EmbedResponse)
async def embed(
    request: EmbedRequest,
    _: Annotated[None, Depends(require_token)] = None,
) -> EmbedResponse:
    """텍스트를 dense 벡터로 — 응답 순서는 입력 순서와 1:1이다.

    한 요청의 개수 상한은 리랭킹과 같은 ``GPU_MAX_PASSAGES``를 쓴다. 색인은 수천 건을
    보내므로 **호출부가 잘라서 보내야 한다** - 한 요청에 다 담으면 요청 하나가
    타임아웃 전체를 잡아먹고, 실패했을 때 어디까지 됐는지도 알 수 없다.
    """
    if embed_service is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="임베딩이 꺼져 있습니다 (GPU_EMBED_MODEL_DIR 미설정)",
        )
    if len(request.texts) > config.max_passages:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"texts {len(request.texts)} > 상한 {config.max_passages}",
        )
    if not embed_service.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="embed model not loaded"
        )

    t0 = time.perf_counter()
    vectors = await embed_service.embed(request.texts)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "embed n=%d elapsed_ms=%s gpu=%s", len(request.texts), elapsed_ms, embed_service.on_gpu
    )
    return EmbedResponse(
        vectors=vectors,
        dimension=embed_service.dimension,
        device="cuda" if embed_service.on_gpu else "cpu",
        elapsed_ms=elapsed_ms,
    )
