"""Cross-encoder reranker clients.

Provides an abstract :class:`RerankerClient` interface and a concrete
:class:`BgeRerankerV2M3Client` adapter backed by the ONNX INT8 build
produced by ``scripts/setup_bge_reranker.py``.

The adapter scores ``(query, passage)`` pairs as a cross-encoder: each pair
is fed as a single tokenized sequence (``[CLS] query [SEP] passage [SEP]``),
yielding one logit per pair which is squashed through sigmoid into ``[0, 1]``.
Sorting and top-k truncation are caller responsibilities — see
``score_pairs`` for the rationale.
"""

from __future__ import annotations

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


class RerankerClient(ABC):
    """Abstract cross-encoder reranker.

    Implementations score each ``(query, passage)`` pair and return one
    float per passage in input order. Sorting, top-k, and the no-op
    ``reranker_enabled=False`` branch are caller concerns.
    """

    @abstractmethod
    async def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        """Return one ``[0, 1]`` score per passage, aligned with input order."""


class BgeRerankerV2M3Client(RerankerClient):
    """BGE Reranker v2-m3 ONNX INT8 client.

    Backed by the INT8 model produced in ``reports/bge_reranker_setup.md``
    (logit |Δ| ≤ 0.028 vs PyTorch, 0.71s/50쌍 on CPU). Long passages are
    truncated tail-first via ``truncation="only_second"`` so the query is
    always preserved verbatim — cross-encoders need the full question
    visible to the attention stack to score relevance correctly.
    """

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
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(resolved_path))
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

        scores: list[float] = []
        for start in range(0, len(passages), self._batch_size):
            chunk = passages[start : start + self._batch_size]
            logits = self._score_batch(query, chunk)
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
        enc = self._tokenizer(
            queries,
            passages,
            padding=True,
            # passage만 자르고 query는 보존 — cross-encoder에서 query 절단은 의미 손상이 큼.
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
