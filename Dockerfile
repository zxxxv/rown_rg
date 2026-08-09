# Phase 3 이후 배포용
FROM python:3.12-slim

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# lxml·xmlsec는 no-binary(소스 빌드)로 고정돼 있어(pyproject [tool.uv]: SAML의 xmlsec가
# 시스템 libxml2와 링크를 맞춰야 런타임 크래시가 안 남) 빌드에 개발 헤더가 필요하다.
# 단일 스테이지라 런타임 크립토 백엔드(libxmlsec1-openssl)도 함께 남는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config \
    libxml2-dev libxslt1-dev libxmlsec1-dev libxmlsec1-openssl libssl-dev \
    && rm -rf /var/lib/apt/lists/*

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
