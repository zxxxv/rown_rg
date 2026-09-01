# Phase 3 이후 배포용
FROM python:3.12-slim

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config zlib1g-dev \
    libxml2-dev libxslt1-dev libxmlsec1-dev libxmlsec1-openssl libssl-dev \
    # 본문 차트를 PNG로 그릴 때 쓰는 한글 폰트 — 없으면 축·범례 라벨이 네모로 깨진다.
    fonts-nanum \
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

# 비root 유저 + 런타임 쓰기 경로 소유권.
# /app 자체를 appuser 소유로 둬서(비재귀 — 기존 root 파일은 그대로) 앱이 런타임에
# 상대경로 디렉터리(cassettes 등)를 자유롭게 만들 수 있게 한다. 명시 경로도 함께 생성.
RUN useradd --create-home appuser \
    && mkdir -p /app/cache /app/exports /app/data/library /app/cassettes \
    && chown appuser:appuser /app \
    && chown -R appuser:appuser /app/cache /app/exports /app/data /app/cassettes
USER appuser

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
