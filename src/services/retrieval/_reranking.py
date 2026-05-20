"""Reranker fusion helper — cross-encoder rescoring on ``SearchHit`` lists.

Sits one layer above the adapter (:class:`RerankerClient`). The adapter only
scores ``(query, passage)`` pairs; this helper does the search-domain glue —
calls the adapter, sorts, truncates, and stamps ``score`` / ``score_source``
on returned hits. Original backend scores are preserved in
``metadata["original_score"]`` and ``metadata["original_score_source"]`` so
downstream consumers can still see what the fan-out backends produced.
"""

from __future__ import annotations

import time

import structlog

from src.clients.reranker_client import RerankerClient
from src.services.retrieval.base import SearchHit

logger = structlog.get_logger(__name__)


async def rerank_hits(
    reranker: RerankerClient,
    query: str,
    hits: list[SearchHit],
    top_k: int = 10,
) -> list[SearchHit]:
    """Rescore ``hits`` with ``reranker``, sort descending, truncate to ``top_k``.

    Args:
        reranker: Cross-encoder scoring client.
        query: Search query, passed verbatim to ``reranker.score_pairs``.
        hits: Candidates from upstream retrieval (hybrid/keyword/semantic).
        top_k: Max hits to return after reranking. Defaults to 10.

    Returns:
        New ``SearchHit`` objects with reranker scores. The original score
        and its ``score_source`` are stashed under ``metadata`` keys so
        callers can audit the upstream backend's view.
    """
    if not hits:
        logger.info("reranker.rerank_hits.empty_input")
        return []

    logger.info(
        "reranker.rerank_hits.started",
        n_hits=len(hits),
        top_k=top_k,
        query_len=len(query),
    )
    t0 = time.perf_counter()

    scores = await reranker.score_pairs(query, [h.content for h in hits])

    rescored: list[SearchHit] = []
    for hit, new_score in zip(hits, scores, strict=True):
        # 원본 점수 보존 — 호출자가 reranker가 어떤 hit에 어떻게 reorder했는지 audit 가능.
        merged_metadata = {
            **hit.metadata,
            "original_score": hit.score,
            "original_score_source": hit.score_source,
        }
        rescored.append(
            hit.model_copy(
                update={
                    "score": new_score,
                    "score_source": "reranker",
                    "metadata": merged_metadata,
                }
            )
        )

    rescored.sort(key=lambda h: h.score, reverse=True)
    result = rescored[:top_k]

    logger.info(
        "reranker.rerank_hits.completed",
        n_input=len(hits),
        n_returned=len(result),
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    return result
