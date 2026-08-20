"""GPU 추론 서비스 설정 — 환경변수만 읽는다.

앱(``src.core.config``)의 Settings를 쓰지 않는 이유: 그쪽은 DB·LLM 키·SAML까지
요구해서 추론 전용 컨테이너에서는 채울 수 없다. 이 서비스가 아는 것은 모델 경로와
포트, 토큰뿐이어야 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ServiceConfig:
    model_dir: str
    device: str
    batch_size: int
    max_length: int
    token: str
    allow_anon: bool
    max_passages: int
    max_concurrency: int
    intra_op_threads: int | None = None
    # 임베딩은 리랭킹과 **다른 모델**이다(bge-m3 vs bge-reranker-v2-m3). 같은 카드에
    # 둘 다 올리므로 fp16이 사실상 필수다 - fp32 두 벌은 8GB에 들어가지 않는다
    # (리랭커 fp32 하나가 4,910MiB였다, 2026-08-20 실측).
    embed_model_dir: str = ""
    # 한 forward의 누적 글자 수 상한. 비우면 VRAM 여유분에서 자동으로 고른다.
    # 앱은 이 값을 호스트 RAM에서 고르지만 여기서는 그러면 안 된다 - 이 컨테이너의
    # 제약은 시스템 RAM이 아니라 VRAM이다.
    embed_max_chars: int = 0
    # 대기 중 + 실행 중 요청 수 상한. 넘으면 429로 즉시 돌려보낸다 - 클라이언트가
    # 60초 타임아웃을 버리는 대신 바로 CPU 폴백으로 가게 한다. 0이면 상한 없음.
    max_in_flight: int = 8

    @classmethod
    def from_env(cls) -> ServiceConfig:
        return cls(
            model_dir=os.environ.get("GPU_MODEL_DIR", "/models/bge-reranker-v2-m3-onnx"),
            device=os.environ.get("GPU_DEVICE", "cuda").strip().lower(),
            # GPU는 CPU와 달리 큰 배치가 이득이다(커널 실행 오버헤드 상각).
            # 3060 Ti 8GB · 512토큰 기준 32가 안전선. VRAM이 모자라면 줄인다.
            batch_size=_int("GPU_BATCH_SIZE", 32),
            max_length=_int("GPU_MAX_LENGTH", 512),
            token=os.environ.get("GPU_TOKEN", "").strip(),
            allow_anon=_bool("GPU_ALLOW_ANON"),
            # 한 요청에 들어올 수 있는 후보 수 상한. 운영 실측은 절당 96개다.
            max_passages=_int("GPU_MAX_PASSAGES", 512),
            # GPU 하나에 전방계산을 여러 개 겹쳐도 빨라지지 않고 VRAM만 위험해진다.
            # 요청은 큐에서 기다리게 하고 순서대로 처리한다.
            max_concurrency=_int("GPU_MAX_CONCURRENCY", 1),
            # GPU 실행에서는 CPU 스레드가 거의 안 쓰이지만, CPU 폴백이나 개발용으로
            # 띄울 때 전 코어를 잡으면 그 PC의 다른 작업이 굳는다. 0이면 코어 수 - 1.
            intra_op_threads=_int("GPU_INTRA_OP_THREADS", 0) or None,
            # 비우면 임베딩 엔드포인트를 띄우지 않는다 - 리랭커만 쓰는 배포에서
            # 없는 모델을 찾다가 컨테이너가 죽는 것을 막는다.
            embed_model_dir=os.environ.get("GPU_EMBED_MODEL_DIR", "").strip(),
            embed_max_chars=_int("GPU_EMBED_MAX_CHARS", 0),
            max_in_flight=_int("GPU_MAX_IN_FLIGHT", 8),
        )

    def validate(self) -> None:
        """뜨기 전에 치명적 설정 오류를 잡는다 — 뜬 뒤에 알면 늦다."""
        if not self.token and not self.allow_anon:
            raise RuntimeError(
                "GPU_TOKEN이 비어 있습니다. 이 엔드포인트로는 자료 본문이 오갑니다. "
                "공유 시크릿을 넣거나, 로컬 실험이라면 GPU_ALLOW_ANON=1을 명시하세요."
            )
        if self.device not in {"cuda", "cpu"}:
            raise RuntimeError(f"GPU_DEVICE는 cuda 또는 cpu여야 합니다: {self.device}")

    @property
    def embed_enabled(self) -> bool:
        """임베딩 모델 경로가 지정됐을 때만 /v1/embed를 연다."""
        return bool(self.embed_model_dir)
