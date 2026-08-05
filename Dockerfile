# Phase 3 이후 배포용
FROM python:3.12-slim

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 먼저 설치 (캐시 활용)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 모델 가중치 굽기 (임베딩 + 리랭커, ~1.1GB) — 코드보다 먼저 복사해
# 코드 변경 시 이 거대한 레이어를 재복사하지 않도록 빌드 캐시를 살린다.
# models/는 .gitignore 대상이라 git clone엔 없다 — 빌드 호스트 디스크에 있어야 한다.
COPY models/ models/

# alembic 설정 (마이그레이션 실행에 필요. 마이그레이션 스크립트는 src/db/migrations로 포함)
COPY alembic.ini ./

# 소스 복사 (자주 바뀌므로 마지막)
COPY src/ src/

# 비root 유저 + 임베딩 디스크 캐시 쓰기 경로 확보
RUN useradd --create-home appuser \
    && mkdir -p /app/cache \
    && chown -R appuser:appuser /app/cache
USER appuser

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
