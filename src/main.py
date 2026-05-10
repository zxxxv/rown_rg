from fastapi import FastAPI

from src.core.config import settings

app = FastAPI(
    title="주식회사 로운인사이트 — AI 보고서 자동생성 시스템",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "environment": settings.environment}
