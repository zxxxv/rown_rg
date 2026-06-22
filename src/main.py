from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.middleware.error_handler import register_error_handlers
from src.api.middleware.ip_whitelist import IPWhitelistMiddleware
from src.api.middleware.logging import LoggingMiddleware
from src.api.routers import api_v1_router
from src.core.config import settings
from src.core.logging import configure_logging
from src.db.session import async_engine

load_dotenv()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield
    await async_engine.dispose()


app = FastAPI(
    title="주식회사 로운인사이트 — AI 보고서 자동생성 시스템",
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


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "environment": settings.environment}
