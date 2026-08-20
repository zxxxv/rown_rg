"""임베딩 모델 보유·인코딩 — 앱과 같은 알맹이(``OnnxTextEmbedder``)를 쓴다.

리랭킹과 분리한 이유는 모델이 다르기 때문이다(bge-m3 vs bge-reranker-v2-m3).
다만 세마포어는 리랭킹과 **공유한다** — 둘 다 같은 8GB 카드를 쓰므로 동시에
돌면 활성화 텐서가 두 배로 필요해진다. ``main.py``가 하나를 만들어 양쪽에 준다.
"""

from __future__ import annotations

import asyncio
import logging
import time

from gpu_service.app.config import ServiceConfig
from gpu_service.app.gpu_queue import GpuQueue
from src.clients.onnx_cross_encoder import CPU_PROVIDERS, CUDA_PROVIDERS
from src.clients.onnx_text_embedder import (
    DIMENSION,
    OnnxTextEmbedder,
    chars_budget_for_bytes,
    clamp_input,
)

logger = logging.getLogger("gpu_service")

# VRAM을 못 읽을 때 쓰는 보수적 상한. 8GB 카드에 모델 둘이 올라간 상태를 가정한다 -
# 크게 잡았다가 색인 도중 OOM이 나면 그 런이 통째로 날아가고, 작게 잡으면 조금 느릴
# 뿐이다. 비대칭이 크므로 모르면 작게 간다.
FALLBACK_MAX_CHARS = 16_000


def free_vram_bytes() -> int | None:
    """현재 가용 VRAM. 읽을 수 없으면 None.

    ``psutil.virtual_memory()``를 쓰면 안 되는 자리다 — 그건 시스템 RAM을 보고,
    이 컨테이너의 제약은 VRAM이다. GPU 박스 RAM이 15.9GB라 시스템 기준으로는
    32,000자가 나오는데 그 값은 8GB VRAM에서 검증된 적이 없다.
    """
    try:
        import pynvml
    except ImportError:
        logger.warning("pynvml이 없어 VRAM을 읽지 못했습니다 - 보수적 기본값을 씁니다")
        return None
    try:
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return int(pynvml.nvmlDeviceGetMemoryInfo(h).free)
        finally:
            pynvml.nvmlShutdown()
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 기본값으로 살린다
        logger.warning("VRAM 조회 실패(%s) - 보수적 기본값을 씁니다", type(exc).__name__)
        return None


def resolve_max_chars(configured: int) -> int:
    """배치 글자 수 상한 결정 — 설정값이 있으면 그것, 없으면 VRAM에서 유도."""
    if configured > 0:
        return configured
    free = free_vram_bytes()
    if free is None:
        return FALLBACK_MAX_CHARS
    # 여유 VRAM 전부를 활성화 텐서에 쓸 수는 없다. 절반만 예산으로 잡는다 -
    # 나머지는 ORT의 임시 버퍼와 다른 모델(리랭커)의 여유분이다.
    return chars_budget_for_bytes(free // 2)


class EmbedService:
    def __init__(
        self, config: ServiceConfig, *, queue: GpuQueue | None = None
    ) -> None:
        self._config = config
        self._embedder: OnnxTextEmbedder | None = None
        self._queue = queue or GpuQueue(config.max_concurrency, config.max_in_flight)
        self._warmup_ms: float | None = None
        self._max_chars: int = 0

    @property
    def ready(self) -> bool:
        return self._embedder is not None

    @property
    def providers(self) -> list[str]:
        return self._embedder.providers if self._embedder else []

    @property
    def on_gpu(self) -> bool:
        """CUDA를 요청했는데 조용히 CPU로 떨어졌는지 — ORT는 이때 예외를 안 던진다."""
        return "CUDAExecutionProvider" in self.providers

    @property
    def warmup_ms(self) -> float | None:
        return self._warmup_ms

    @property
    def max_chars(self) -> int:
        return self._max_chars

    @property
    def dimension(self) -> int:
        return DIMENSION

    def load(self) -> None:
        """모델 적재 + 워밍업 1회. 실패하면 예외를 그대로 올려 컨테이너를 죽인다."""
        t0 = time.perf_counter()
        # 상한은 모델을 올리기 **전에** 정한다. 적재 후에 재면 가중치가 이미 차지한
        # 만큼 여유가 줄어 상한이 과소평가된다.
        self._max_chars = resolve_max_chars(self._config.embed_max_chars)
        providers = CUDA_PROVIDERS if self._config.device == "cuda" else CPU_PROVIDERS
        self._embedder = OnnxTextEmbedder(
            self._config.embed_model_dir,
            max_length=self._config.max_length,
            max_chars_per_batch=self._max_chars,
            providers=providers,
            intra_op_num_threads=self._config.intra_op_threads,
        )
        # 첫 요청이 커널 컴파일·메모리 할당을 뒤집어쓰지 않게 미리 한 번 돌린다.
        self._embedder.embed(["워밍업 문장"])
        self._warmup_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "embed model loaded: dir=%s providers=%s max_chars=%d warmup_ms=%s",
            self._config.embed_model_dir,
            self.providers,
            self._max_chars,
            self._warmup_ms,
        )
        if self._config.device == "cuda" and not self.on_gpu:
            logger.error(
                "CUDA를 요청했으나 CPUExecutionProvider로 떨어졌습니다. providers=%s",
                self.providers,
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            raise RuntimeError("임베딩 모델이 아직 적재되지 않았습니다")
        embedder = self._embedder
        # 4,000자 hard cap은 여기서도 건다. 앱이 이미 걸고 보내지만, 이 엔드포인트를
        # 다른 호출자가 쓰면 토크나이저가 통째 입력을 메모리에 올려 터진다.
        clamped = clamp_input(texts)
        async with self._queue.acquire():
            # 전방계산은 스레드로. 안 그러면 이벤트 루프가 멈춰 /health조차 응답이 없다.
            vectors = await asyncio.to_thread(embedder.embed, clamped)
        return vectors.tolist()
