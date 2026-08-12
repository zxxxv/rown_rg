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
from src.core.config import settings
from src.services.qa.alignment import token_similarity
from src.services.retrieval.base import SearchHit

logger = structlog.get_logger(__name__)


def _mmr(ranked: list[SearchHit], top_k: int) -> list[SearchHit]:
    """MMR — 관련성만 보고 뽑던 상위 k를 '관련성 - 이미 뽑은 것과의 중복'으로 다시 고른다.

    실측(2026-08-12, 14절 보고서)에서 시장조사 보고서의 총론 문단 몇 개가 절마다
    상위를 차지했다. 리랭커는 질의와 표면적으로 가까운 걸 올리므로, 비슷한 문단이
    여럿이면 그것들이 자리를 나눠 갖는다. 그 사이 국내 실측 수치가 든 청크는
    한 번도 안 뽑혔다.

    유사도는 어휘 겹침(글자 2-gram + 수치·영문)을 쓴다. 임베딩을 다시 돌리지 않아
    비용이 0이고, 우리가 잡으려는 중복이 '거의 그대로 복사된 문단'이라 잘 맞는다
    (절 간 중복 검출이 같은 자로 102쌍을 잡았다).

    λ=1이면 원래 동작(관련성만)이라 끄는 것과 같다. 낮출수록 다양성을 사지만
    관련성을 판다 - 기본값은 보수적으로 두고 실측으로 조정한다.
    """
    if len(ranked) <= top_k:
        return ranked
    lam = settings.mmr_lambda
    # 다양성은 '관련 있는 것들 사이에서만' 산다. 하한이 없으면 리랭커가 관련 없다고
    # 판정한 청크를 다양성 점수로 끌어올린다 - 실측(2026-08-12)에서 고유 청크는
    # 194→218로 늘었지만 선택된 청크 점수의 하위 10%가 0.011→0.001로 무너졌다.
    # 하한 아래는 후보에서 빼고, 후보가 top_k에 못 미치면 다양화를 포기한다.
    eligible = [i for i, h in enumerate(ranked) if h.score >= settings.mmr_min_score]
    if len(eligible) <= top_k:
        return ranked[:top_k]
    tokens = [_weighted_tokens_cached(h.content) for h in ranked]
    picked: list[int] = [eligible[0]]
    while len(picked) < top_k:
        best_i, best_val = None, float("-inf")
        for i in eligible:
            hit = ranked[i]
            if i in picked:
                continue
            redundancy = max(token_similarity(tokens[i], tokens[j]) for j in picked)
            val = lam * hit.score - (1.0 - lam) * redundancy
            if val > best_val:
                best_i, best_val = i, val
        if best_i is None:
            break
        picked.append(best_i)
    return [ranked[i] for i in picked]


def _weighted_tokens_cached(text: str) -> dict[str, float]:
    from src.services.qa.alignment import weighted_tokens

    return weighted_tokens(text)


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
    result = _mmr(rescored, top_k) if settings.mmr_enabled else rescored[:top_k]

    logger.info(
        "reranker.rerank_hits.completed",
        n_input=len(hits),
        n_returned=len(result),
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    return result
