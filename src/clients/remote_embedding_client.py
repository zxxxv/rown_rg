"""원격 GPU 임베딩 클라이언트 — 벡터 생성을 GPU 박스로 내보낸다.

``RemoteRerankerClient``를 본떴지만 **성격이 다르다**. 리랭커 점수는 순위 결정에만
쓰이고 저장되지 않아 원격/로컬이 조금 달라도 회복이 쉽다. 임베딩은 그 벡터가
**색인에 박힌다** — 원격(fp16)과 로컬(int8)이 만든 벡터가 다른 공간에 놓이면
질의와 색인이 어긋나 검색이 조용히 나빠진다. 에러는 하나도 안 난다.

그래서 이 클라이언트를 켜기 전에 **전량 재색인으로 공간을 통일해야 한다**.
2026-08-20 결정: 프로젝트가 초기 단계라 재색인을 허용하고 GPU로 옮긴다.

폴백에 ``passthrough``가 없는 것도 리랭커와 다른 점이다. 재채점은 건너뛰고 검색
순위를 그대로 쓸 수 있지만, 벡터 생성은 건너뛸 수 없다 — 없으면 검색 자체가 안 된다.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from src.clients.embedding_client import EmbeddingClient, EmbeddingResult
from src.clients.onnx_text_embedder import DIMENSION, clamp_input
from src.clients.remote_stats import RemoteCallStats
from src.core.clock import now
from src.core.config import settings

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

logger = structlog.get_logger(__name__)

FALLBACK_LOCAL = "local"
FALLBACK_ERROR = "error"


class RemoteEmbeddingClient(EmbeddingClient):
    """HTTP로 GPU 추론 서비스에 임베딩을 위임하고, 실패하면 폴백한다.

    **캐시를 두지 않는다.** 로컬 클라이언트는 CPU 재계산이 비싸서 디스크 캐시가
    값어치가 있었지만, GPU에서는 한 건이 수십 ms다. 게다가 캐시를 여기 두면 원격
    모델이 바뀌었을 때 지문 관리가 이중이 되고, 그게 정확히 dtype 혼합을 만드는
    경로다. 재색인은 어차피 전부 캐시 미스라 얻을 것도 없다.
    """

    DIMENSION = DIMENSION

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_s: float | None = None,
        connect_timeout_s: float | None = None,
        cooldown_s: float | None = None,
        chunk: int | None = None,
        fallback: str | None = None,
        client: httpx.AsyncClient | None = None,
        local_factory: Any = None,
    ) -> None:
        self._base_url = (base_url or settings.embedding_remote_url).rstrip("/")
        self._token = token if token is not None else settings.embedding_remote_token
        self._timeout_s = timeout_s or settings.embedding_remote_timeout_s
        self._connect_timeout_s = connect_timeout_s or settings.embedding_remote_connect_timeout_s
        self._cooldown_s = (
            cooldown_s if cooldown_s is not None else settings.embedding_remote_cooldown_s
        )
        self._chunk = chunk or settings.embedding_remote_chunk
        self._fallback = fallback or settings.embedding_remote_fallback
        self._client = client
        # 로컬 폴백 모델은 **처음 실패할 때까지 만들지 않는다**. 원격을 쓰는 이유가
        # 서버에서 모델을 안 올리는 것인데 여기서 미리 만들면 그 이득이 사라진다.
        # bge-m3는 2GB급이라 리랭커보다 이 차이가 크다.
        self._local: EmbeddingClient | None = None
        # 토크나이저는 모델과 별개로 지연 로드한다 - tokenizer 프로퍼티 참조.
        self._tokenizer: PreTrainedTokenizerBase | None = None
        self._tokenizer_lock = threading.Lock()
        self._local_factory = local_factory
        # 색인은 한 번에 수천 번 호출한다. 쿨다운이 없으면 죽은 서비스를 상대로
        # 타임아웃을 수천 번 기다린다 - 리랭커(절당 1회)보다 훨씬 치명적이다.
        self._disabled_until: float = 0.0
        self.stats = RemoteCallStats()

        logger.info(
            "embedding.remote.configured",
            base_url=self._base_url,
            fallback=self._fallback,
            chunk=self._chunk,
            timeout_s=self._timeout_s,
            has_token=bool(self._token),
        )

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        """청킹이 빌려 쓰는 토크나이저 — **모델은 올리지 않는다**.

        ``_chunking``이 토큰 수를 세려고 임베딩 클라이언트의 토크나이저를 가져간다
        (``_chunking.py``의 ``embedding_client.tokenizer``). 원격을 쓴다고 이걸 못
        내놓으면 청킹이 통째로 막힌다.

        그래서 토크나이저 파일만 지연 로드한다. 17MB이고 ONNX 세션을 만들지 않으므로
        2GB 모델을 올리는 것과 완전히 다른 비용이다 - 원격화의 목적(서버에서 모델
        메모리를 안 쓰는 것)을 해치지 않는다.

        로컬 모델 폴더에서 읽는 이유: bge-m3의 토크나이저는 dtype과 무관하게 같다.
        int8 폴더의 tokenizer.json과 fp16 폴더의 것이 동일하므로 원격 모델과 어긋나지
        않는다. 만약 원격이 **다른 모델**이 되면 그때는 이 가정이 깨진다.
        """
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            path = Path(settings.embedding_model_path)
            logger.info("embedding.remote.tokenizer.loading", path=str(path))
            self._tokenizer = AutoTokenizer.from_pretrained(str(path))  # type: ignore[no-untyped-call]
        return self._tokenizer

    @property
    def tokenizer_lock(self) -> threading.Lock:
        """토크나이저 직렬화 락 — HF fast tokenizer는 동시 호출에 죽는다.

        원격을 쓰면 추론은 밖으로 나가지만 **토큰화는 여전히 이 프로세스에서** 돈다.
        따라서 "Already borrowed" 위험도 그대로다(2026-08-12 실사고).
        """
        return self._tokenizer_lock

    async def embed(self, text: str) -> EmbeddingResult:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        if not texts:
            return []
        # 4,000자 hard cap을 **보내기 전에** 건다. 토크나이저 중간 구조가 통째 입력을
        # 메모리에 올려 터지는 문제라 네트워크 너머로 미루면 GPU 서비스가 죽는다.
        clamped = clamp_input(texts)

        if self._in_cooldown():
            return await self._fallback_embed(clamped, reason="cooldown")

        t0 = time.perf_counter()
        try:
            vectors = await self._request_vectors(clamped)
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 폴백으로 살린다
            # 429(GPU 큐 포화)는 **쿨다운을 걸지 않는다**. 서비스가 죽은 게 아니라
            # 잠깐 밀린 것이라, 60초를 통째로 건너뛰면 순간적인 몰림 때문에 그 뒤의
            # 한가한 1분까지 CPU로 처리하게 된다. 429는 이미 즉시 돌아오므로
            # 매번 시도해도 비용이 거의 없다.
            busy = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            if not busy:
                self._disabled_until = time.monotonic() + self._cooldown_s
            self.stats.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning(
                "embedding.remote.failed",
                error=type(exc).__name__,
                detail=str(exc)[:200],
                n_texts=len(clamped),
                cooldown_s=0 if busy else self._cooldown_s,
                busy=busy,
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return await self._fallback_embed(clamped, reason="error")

        self.stats.record_ok()
        logger.info(
            "embedding.remote.completed",
            n_texts=len(clamped),
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return [
            EmbeddingResult(embedding=v, text=t, cached=False)
            for v, t in zip(vectors, clamped, strict=True)
        ]

    async def _request_vectors(self, texts: list[str]) -> list[list[float]]:
        """청크로 나눠 순차 요청 — 순서를 보존해 이어 붙인다.

        동시에 보내지 않는 이유: GPU 서비스의 세마포어가 1이라 어차피 줄을 선다.
        동시 요청은 큐만 깊게 만들고, 실패했을 때 어디까지 됐는지 알기 어려워진다.
        """
        client = self._ensure_client()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        out: list[list[float]] = []
        for start in range(0, len(texts), self._chunk):
            batch = texts[start : start + self._chunk]
            # 벨트+서스펜더: httpx 타임아웃이 있어도, 터널이 연결을 끊은 소켓의
            # 대기가 깨어나지 못한 채 코루틴이 영구 정지한 실사례가 있다
            # (2026-08-21 v6 색인 25분 정지, Windows Proactor). 코루틴 차원의
            # 상한을 한 겹 더 - 초과는 실패로 올라가 쿨다운+폴백을 탄다.
            response = await asyncio.wait_for(
                client.post(
                    f"{self._base_url}/v1/embed",
                    json={"texts": batch},
                    headers=headers,
                ),
                timeout=self._timeout_s + 30,
            )
            response.raise_for_status()
            out.extend(self._validate(response.json(), len(batch)))
        return out

    @staticmethod
    def _validate(payload: Any, expected: int) -> list[list[float]]:
        """응답을 믿기 전에 개수·차원·유한성을 확인한다.

        차원까지 보는 것이 리랭커와 다른 점이다. 개수만 맞고 차원이 다른 벡터가
        색인에 들어가면 그때부터 유사도 계산이 통째로 어긋나는데, 저장된 뒤에는
        어디서부터 잘못됐는지 되짚기가 매우 어렵다. NaN도 마찬가지다 - 한 건만
        섞여도 그 벡터와의 비교가 전부 오염된다.
        """
        if not isinstance(payload, dict):
            raise ValueError(f"응답이 객체가 아님: {type(payload).__name__}")
        vectors = payload.get("vectors")
        if not isinstance(vectors, list):
            raise ValueError("응답에 vectors 배열이 없음")
        if len(vectors) != expected:
            raise ValueError(f"벡터 개수 불일치: {len(vectors)} != {expected}")
        out: list[list[float]] = []
        for i, v in enumerate(vectors):
            if not isinstance(v, list) or len(v) != DIMENSION:
                got = len(v) if isinstance(v, list) else type(v).__name__
                raise ValueError(f"{i}번 벡터 차원 불일치: {got} != {DIMENSION}")
            for x in v:
                if isinstance(x, bool) or not isinstance(x, (int, float)):
                    raise ValueError(f"{i}번 벡터에 수가 아닌 값: {x!r}")
                if not math.isfinite(x):
                    raise ValueError(f"{i}번 벡터에 유한하지 않은 값: {x!r}")
            out.append([float(x) for x in v])
        return out

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s, connect=self._connect_timeout_s),
            )
        return self._client

    def _in_cooldown(self) -> bool:
        return time.monotonic() < self._disabled_until

    def stats_snapshot(self) -> dict[str, Any]:
        """모니터 라우터 노출용 — 폴백 누적과 쿨다운 여부.

        ``fallback_items_total``이 핵심이다. local 폴백으로 만들어진 벡터 수 =
        dtype이 다른 채 색인에 들어갔을 수 있는 벡터 수라, 0이 아니면 재색인을
        검토해야 한다는 신호다.
        """
        return {
            "mode": "remote",
            "fallback_policy": self._fallback,
            "base_url": self._base_url,
            **self.stats.snapshot(disabled_until_monotonic=self._disabled_until),
        }

    async def _fallback_embed(self, texts: list[str], *, reason: str) -> list[EmbeddingResult]:
        self.stats.record_fallback(reason, items=len(texts))
        if self._fallback == FALLBACK_ERROR:
            logger.error(
                "embedding.remote.fallback.error",
                reason=reason,
                n_texts=len(texts),
                at=now().isoformat(),
            )
            raise RuntimeError(
                f"원격 임베딩 실패({reason}). fallback=error라 로컬로 내려가지 않습니다 — "
                "로컬 CPU 모델은 dtype이 달라 색인과 다른 공간의 벡터를 만듭니다."
            )

        # local 폴백은 dtype 불일치를 감수한다. 사용자 결정(2026-08-20)이며, 검색이
        # 조용히 나빠지는 대신 서비스가 멈추지 않는 쪽을 택한 것이다. 그래서 warning으로
        # 남긴다 - 이 로그가 쌓이면 색인 품질을 의심할 근거가 된다.
        logger.warning(
            "embedding.remote.fallback.local",
            reason=reason,
            n_texts=len(texts),
            at=now().isoformat(),
        )
        return await self._local_client().embed_batch(texts)

    def _local_client(self) -> EmbeddingClient:
        if self._local is None:
            if self._local_factory is not None:
                self._local = self._local_factory()
            else:
                from src.clients.embedding_client import BgeM3Client

                self._local = BgeM3Client()
        return self._local

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
