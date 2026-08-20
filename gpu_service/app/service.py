"""모델 보유·채점 — 앱과 같은 알맹이(``OnnxCrossEncoder``)를 쓴다."""

from __future__ import annotations

import asyncio
import logging
import time

from gpu_service.app.config import ServiceConfig
from src.clients.onnx_cross_encoder import (
    CPU_PROVIDERS,
    CUDA_PROVIDERS,
    OnnxCrossEncoder,
)

logger = logging.getLogger("gpu_service")


class RerankService:
    def __init__(
        self, config: ServiceConfig, *, semaphore: asyncio.Semaphore | None = None
    ) -> None:
        self._config = config
        self._encoder: OnnxCrossEncoder | None = None
        # 세마포어를 밖에서 받는 이유: 임베딩 서비스와 **같은 카드**를 쓴다. 각자
        # 하나씩 가지면 리랭킹과 색인이 겹쳐 돌아 VRAM이 두 배로 필요해진다.
        self._semaphore = semaphore or asyncio.Semaphore(config.max_concurrency)
        self._warmup_ms: float | None = None

    @property
    def ready(self) -> bool:
        return self._encoder is not None

    @property
    def providers(self) -> list[str]:
        return self._encoder.providers if self._encoder else []

    @property
    def warmup_ms(self) -> float | None:
        return self._warmup_ms

    @property
    def on_gpu(self) -> bool:
        """CUDA를 요청했는데 조용히 CPU로 떨어졌는지 판별.

        ORT는 CUDAExecutionProvider 초기화가 실패해도 예외를 안 던지고 CPU로 내려간다.
        그러면 서비스는 멀쩡히 뜨는데 속도만 안 나서, 원인을 엉뚱한 데서 찾게 된다.
        /health가 이걸 그대로 노출한다.
        """
        return "CUDAExecutionProvider" in self.providers

    def load(self) -> None:
        """모델 적재 + 워밍업 1회. 실패하면 예외를 그대로 올려 컨테이너를 죽인다."""
        t0 = time.perf_counter()
        providers = CUDA_PROVIDERS if self._config.device == "cuda" else CPU_PROVIDERS
        self._encoder = OnnxCrossEncoder(
            self._config.model_dir,
            batch_size=self._config.batch_size,
            max_length=self._config.max_length,
            providers=providers,
            intra_op_num_threads=self._config.intra_op_threads,
        )
        # 첫 요청이 커널 컴파일·메모리 할당을 뒤집어쓰지 않게 미리 한 번 돌린다.
        # 이걸 안 하면 첫 절만 유독 느려 원격 타임아웃에 걸린다.
        self._encoder.score("워밍업 질의", ["워밍업 문단"])
        self._warmup_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "model loaded: dir=%s providers=%s warmup_ms=%s",
            self._config.model_dir,
            self.providers,
            self._warmup_ms,
        )
        if self._config.device == "cuda" and not self.on_gpu:
            # 죽이지는 않는다 - CPU로라도 도는 편이 서비스 중단보다 낫다. 대신 시끄럽게 남긴다.
            logger.error(
                "CUDA를 요청했으나 CPUExecutionProvider로 떨어졌습니다. "
                "providers=%s — nvidia 런타임·드라이버·onnxruntime-gpu 조합을 확인하세요.",
                self.providers,
            )

    async def score(self, query: str, passages: list[str]) -> list[float]:
        if self._encoder is None:
            raise RuntimeError("모델이 아직 적재되지 않았습니다")
        encoder = self._encoder
        async with self._semaphore:
            # 전방계산은 스레드로. 안 그러면 이벤트 루프가 멈춰 /health조차 응답이 없다.
            return await asyncio.to_thread(encoder.score, query, passages)
