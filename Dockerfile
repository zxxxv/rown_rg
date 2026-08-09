# Phase 3 이후 배포용
FROM python:3.12-slim

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config zlib1g-dev \
    libxml2-dev libxslt1-dev libxmlsec1-dev libxmlsec1-openssl libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 의존성만 먼저 설치 (캐시 활용). pyproject가 README.md를 참조하고 uv가 프로젝트를 빌드하려
# 하므로, 이 단계에선 --no-install-project 로 의존성만 받는다(README·src는 아직 없음).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 모델 가중치 굽기 (임베딩 + 리랭커, ~1.1GB) — 코드보다 먼저 복사해
# 코드 변경 시 이 거대한 레이어를 재복사하지 않도록 빌드 캐시를 살린다.
# models/는 .gitignore 대상이라 git clone엔 없다 — 빌드 호스트 디스크에 있어야 한다.
COPY models/ models/

# alembic 설정 + README.md(pyproject.toml의 readme 참조 — 프로젝트 설치에 필요)
COPY alembic.ini README.md ./

# 소스 복사 (자주 바뀌므로 마지막)
COPY src/ src/

# 이제 프로젝트 자체 설치 (src·README 준비됨 — 위 deps 레이어는 캐시 재사용)
RUN uv sync --frozen --no-dev

# uv run 이 런타임에 환경을 다시 sync하지 않게 — 이미지의 .venv(빌드 시 완성)를 그대로 쓴다.
# 안 그러면 appuser가 root 소유 .venv를 수정하려다 Permission denied + dev 의존성까지 내려받는다.
ENV UV_NO_SYNC=1

# 비root 유저 + 앱이 런타임에 쓰는 경로(임베딩 캐시·산출물·업로드) 소유권 확보
RUN useradd --create-home appuser \
    && mkdir -p /app/cache /app/exports /app/data/library \
    && chown -R appuser:appuser /app/cache /app/exports /app/data
USER appuser

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
