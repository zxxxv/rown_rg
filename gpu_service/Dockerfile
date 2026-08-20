# CUDA 런타임 + cuDNN 9 (onnxruntime-gpu 1.20 요구). 태그는 드라이버에 맞춰 바꿀 수 있게 ARG.
# CUDA 12는 마이너 버전 호환이라 드라이버 525 이상이면 12.x 런타임이 모두 돈다.
ARG CUDA_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04
FROM ${CUDA_IMAGE}

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY gpu_service/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# /v1/parse의 multipart 수신용. requirements.txt에 안 넣고 별도 레이어로 두는 이유:
# 위 레이어는 torch 포함 ~3GB 설치라, requirements가 한 줄만 바뀌어도 통째로
# 다시 받는다. 작은 추가 의존성은 여기 얹어 캐시를 지킨다.
RUN pip install --no-cache-dir python-multipart==0.0.20
# opencv(docling 파이프라인이 rapidocr 경유로 임포트)의 네이티브 의존성. 앞의 apt 레이어에
# 합치지 않는 이유: 그 레이어를 건드리면 pip 레이어까지 전부 재빌드된다(위 주석과 같은 사정).
# 로드 시점엔 안 터지고 첫 /v1/parse에서 ImportError: libxcb.so.1로 드러난다(2026-08-21 실측).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 앱 코드 중 **채점 알맹이만** 들어온다. src.core.config가 딸려오면 이미지가 깨진다
# (DB·LLM 키를 요구한다). onnx_cross_encoder.py의 맨 위 계약이 그 이유다.
COPY src/__init__.py src/__init__.py
COPY src/clients/__init__.py src/clients/onnx_cross_encoder.py src/clients/onnx_text_embedder.py src/clients/
COPY gpu_service gpu_service

# 모델은 볼륨으로 마운트한다 — 이미지에 2GB를 굽지 않는다.
# 오프라인 고정: 토크나이저가 허깅페이스로 나가려 하면 즉시 실패하게 해 원인을 드러낸다.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Ubuntu 24.04 베이스 이미지에는 uid 1000이 이미 있다(ubuntu 사용자). 그대로 useradd하면
# exit 4(UID already in use)로 빌드가 죽는다 — 22.04에서는 없던 충돌이다. 먼저 걷어낸다.
RUN userdel -r ubuntu 2>/dev/null || true; useradd --create-home --uid 1000 rown && chown -R rown /app
USER rown

EXPOSE 8009
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://localhost:8009/health || exit 1

CMD ["uvicorn", "gpu_service.app.main:app", "--host", "0.0.0.0", "--port", "8009"]
