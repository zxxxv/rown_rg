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

WORKDIR /app
# 앱 코드 중 **채점 알맹이만** 들어온다. src.core.config가 딸려오면 이미지가 깨진다
# (DB·LLM 키를 요구한다). onnx_cross_encoder.py의 맨 위 계약이 그 이유다.
COPY src/__init__.py src/__init__.py
COPY src/clients/__init__.py src/clients/onnx_cross_encoder.py src/clients/
COPY gpu_service gpu_service

# 모델은 볼륨으로 마운트한다 — 이미지에 2GB를 굽지 않는다.
# 오프라인 고정: 토크나이저가 허깅페이스로 나가려 하면 즉시 실패하게 해 원인을 드러낸다.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN useradd --create-home --uid 1000 rown && chown -R rown /app
USER rown

EXPOSE 8009
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://localhost:8009/health || exit 1

CMD ["uvicorn", "gpu_service.app.main:app", "--host", "0.0.0.0", "--port", "8009"]
