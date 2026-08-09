from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware.error_handler import register_error_handlers
from src.api.middleware.ip_whitelist import IPWhitelistMiddleware
from src.api.middleware.logging import LoggingMiddleware
from src.api.routers import api_v1_router
from src.api.routers.notify import router as notify_router
from src.api.routers.ws import router as ws_router
from src.core.config import settings
from src.core.logging import configure_logging
from src.db.session import async_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # 관리자 설정(app_settings) 인메모리 캐시 로드 — .env 오버라이드를 반영.
    # 테이블이 아직 없거나(마이그레이션 전) DB 미가용이면 조용히 넘어간다(env만 사용).
    try:
        from src.core import app_settings

        await app_settings.refresh_cache()
    except Exception:
        pass
    yield
    await async_engine.dispose()


app = FastAPI(
    title="주식회사 로운인사이트 - AI 보고서 자동생성 시스템",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)
app.add_middleware(IPWhitelistMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(api_v1_router)
app.include_router(notify_router, prefix="/api/v1")
# 진행 상황 WebSocket — 앱 루트(/ws/...), same-origin. /api/v1 아님.
app.include_router(ws_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    # 환경 문자열은 노출하지 않는다(정보 최소화) — 로드밸런서/헬스체크엔 status면 충분.
    return {"status": "healthy"}
