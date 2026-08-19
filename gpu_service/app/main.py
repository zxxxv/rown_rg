"""GPU 리랭킹 서비스 — FastAPI.

AWS 앱의 ``RemoteRerankerClient``가 이 서비스를 부른다. 계약은 둘뿐이다:

- ``GET  /health``       — 인증 없음. 모델 적재 여부와 **실제 실행 공급자**를 돌려준다.
- ``POST /v1/rerank``    — Bearer 토큰. ``{query, passages}`` → ``{scores}``.

응답 ``scores``는 입력 ``passages`` 순서와 1:1로 맞아야 한다 — 앱이
``zip(hits, scores, strict=True)``로 묶기 때문에 개수가 어긋나면 검색 한복판에서
터진다. 앱 쪽에도 검증이 있지만 여기서 먼저 지킨다.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from gpu_service.app.config import ServiceConfig
from gpu_service.app.service import RerankService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("gpu_service")

config = ServiceConfig.from_env()
service = RerankService(config)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    passages: list[str] = Field(min_length=1)


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
    yield


app = FastAPI(title="rown GPU reranker", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if service.ready else "loading",
        "ready": service.ready,
        "device_requested": config.device,
        "on_gpu": service.on_gpu,
        "providers": service.providers,
        "model_dir": config.model_dir,
        "batch_size": config.batch_size,
        "max_length": config.max_length,
        "warmup_ms": service.warmup_ms,
    }


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
