from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import structlog

from src.core.config import settings

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

logger = structlog.get_logger(__name__)


def _session_options(ort):
    """ONNX Runtime 세션 옵션 — CPU 메모리 아레나를 끈다.

    기본 아레나 할당자는 한 번 커지면 **OS에 돌려주지 않는다**. 배치 안 최장 시퀀스에
    맞춘 패딩으로 중간 텐서가 부풀면(실측 피크 14GB) 그 크기를 프로세스가 계속 붙든다.
    실제로 운영 서버가 CPU 0.2% idle 상태에서 29.4GB/31GB를 점유했다(2026-08-12).
    런이 죽어도 안 줄어들어 다음 런이 OOM으로 죽는다.

    아레나를 끄면 할당마다 malloc/free가 돌아 추론이 조금 느려지지만, 우리는 요청당
    수백 ms 단위 추론을 하루 수천 번 도는 게 아니라 배치 색인을 가끔 돈다 - 메모리를
    돌려받는 쪽이 훨씬 값어치 있다.
    """
    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = False
    return opts


class RerankerClient(ABC):
    @abstractmethod
    async def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        """Return one ``[0, 1]`` score per passage, aligned with input order."""


class BgeRerankerV2M3Client(RerankerClient):
    """BGE Reranker v2-m3 ONNX INT8 client."""

    # XLM-RoBERTa 최대 입력. 한국어 800자 ≈ 400~500 토큰, 512에서 안전 절단.
    MAX_LENGTH: ClassVar[int] = 512
    # cross-encoder는 쿼리당 1배치 latency가 지배 — 너무 큰 배치는 padding 손해.
    # 50쌍 × 512토큰 단발 batch가 측정 기준이라 BATCH_SIZE는 운영 토글로 노출 (settings).
    DEFAULT_BATCH_SIZE: ClassVar[int] = 16

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        batch_size: int | None = None,
        max_length: int | None = None,
    ) -> None:
        """Initialize the BGE Reranker v2-m3 client.

        Args:
            model_path: Directory containing ``model.onnx`` plus tokenizer
                files. Defaults to ``settings.reranker_model_path``.
            batch_size: Override the per-forward batch size. When None,
                falls back to ``settings.reranker_batch_size``.
            max_length: Override tokenizer truncation length. When None,
                falls back to ``settings.reranker_max_length``.
        """
        # 무거운 임포트는 인스턴스 생성 시점까지 미룸 — 모듈 import만으로 onnxruntime·
        # transformers를 끌어오면 단위 테스트 수집 비용이 크게 늘어남.
        import onnxruntime as ort
        from transformers import AutoTokenizer

        resolved_path = Path(model_path or settings.reranker_model_path)
        self._model_dir = resolved_path
        self._batch_size = batch_size or settings.reranker_batch_size
        self._max_length = max_length or settings.reranker_max_length

        logger.info(
            "reranker.client.init.started",
            model_path=str(resolved_path),
            batch_size=self._batch_size,
            max_length=self._max_length,
        )
        t0 = time.perf_counter()
        self._session = ort.InferenceSession(
            str(resolved_path / "model.onnx"),
            sess_options=_session_options(ort),
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(resolved_path))
        # 리랭킹은 asyncio.to_thread로 나가고 절 단위 병렬 작성이 여러 개를 동시에
        # 부른다. HF fast tokenizer는 Rust 백엔드라 동시 호출 시 "Already borrowed"로
        # 죽는다(2026-08-12 색인에서 실제로 터졌다). 토큰화만 직렬화한다 - ONNX 추론은
        # 스레드 안전하고 시간의 대부분이라 그것까지 묶으면 병렬화가 무의미해진다.
        self._tokenizer_lock = threading.Lock()
        logger.info(
            "reranker.client.init.completed",
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        """Expose the HF tokenizer for downstream reuse (e.g. token counting)."""
        return self._tokenizer

    async def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        """Score each ``(query, passage)`` pair and return sigmoid scores.

        Args:
            query: Search query, kept intact (never truncated — see
                ``truncation="only_second"``).
            passages: Candidate passages to score. May be empty.

        Returns:
            List of ``[0, 1]`` scores aligned with ``passages`` order.
            Empty input returns ``[]``.
        """
        if not passages:
            logger.info("reranker.score.empty", query_len=len(query))
            return []

        logger.info(
            "reranker.score.started",
            query_len=len(query),
            n_passages=len(passages),
            batch_size=self._batch_size,
        )
        t0 = time.perf_counter()

        # ONNX 추론은 스레드로 내보낸다. 여기서 그냥 돌리면 절당 85~103초(실측,
        # 패시지 64개) 동안 **이벤트 루프 전체가 멈춘다** - 절 단위 병렬 작성
        # (write_section_concurrency)도, 다른 절의 LLM 응답 수신도 그 사이 아무것도
        # 진행되지 않는다. 실제로 2026-08-12 검증 런의 리랭킹 로그가 완전히 직렬이었다.
        # 임베딩 클라이언트는 처음부터 to_thread를 썼는데 여기만 빠져 있었다.
        # ORT의 InferenceSession.run은 스레드 안전하고 추론 중 GIL을 놓는다.
        scores: list[float] = []
        for start in range(0, len(passages), self._batch_size):
            chunk = passages[start : start + self._batch_size]
            logits = await asyncio.to_thread(self._score_batch, query, chunk)
            scores.extend(self._sigmoid(logits).tolist())

        logger.info(
            "reranker.score.completed",
            n_passages=len(passages),
            n_batches=(len(passages) + self._batch_size - 1) // self._batch_size,
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return scores

    def _score_batch(self, query: str, passages: list[str]) -> np.ndarray:
        """Run one ONNX forward pass over ``(query, passages)`` pairs.

        Returns:
            1-D float32 array of length ``len(passages)`` — raw logits
            (no sigmoid applied).
        """
        queries = [query] * len(passages)
        with self._tokenizer_lock:
            enc = self._tokenizer(
                queries,
                passages,
                padding=True,
                # passage만 자르고 query는 보존 — cross-encoder에서 query 절단은 의미 손상이 크다.
                truncation="only_second",
                max_length=self._max_length,
                return_tensors="np",
            )
        outputs = self._session.run(
            None,
            {
                "input_ids": enc["input_ids"].astype(np.int64),
                "attention_mask": enc["attention_mask"].astype(np.int64),
            },
        )
        # logits shape: (batch, 1) → (batch,)
        return outputs[0].squeeze(-1).astype(np.float32)

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        # 안정 변형: 항상 음수 지수만 평가해 overflow를 차단. x≥0이면 1/(1+exp(-x)),
        # x<0이면 exp(x)/(1+exp(x)). np.where는 양쪽 분기를 모두 평가하므로 -|x|로
        # 마스킹한 뒤 분기별 공식으로 합성한다.
        neg_abs = -np.abs(x)
        e = np.exp(neg_abs)
        return np.where(x >= 0, 1.0 / (1.0 + e), e / (1.0 + e))
