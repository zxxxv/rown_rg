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
from uuid import UUID

import structlog

from src.clients.reranker_client import RerankerClient
from src.core.config import settings
from src.services.qa.alignment import token_similarity
from src.services.retrieval.base import SearchHit

logger = structlog.get_logger(__name__)


# 상위 1위 대비 이 비율 아래는 근거로 싣지 않는다. 절대값(0.05 등)은 질의마다 점수
# 스케일이 흔들려 어떤 절은 다 통과하고 어떤 절은 다 잘린다.
_MIN_KEEP = 3
# 출처별 상한의 하한 — 비율이 아무리 낮아도 한 자료에서 이만큼은 연달아 쓸 수 있다.
# 이게 없으면 캡이 작은 절(top_k가 작을 때)에서 한 자료당 1~2개로 쪼개져 맥락이 끊긴다.
_MIN_PER_SOURCE = 3


def _cap_per_source(ranked: list[SearchHit], cap: int) -> list[SearchHit]:
    """한 출처가 절당 근거 슬롯을 독점하지 못하게 나눈다(순위 순서는 보존).

    리랭커는 청크만 보고 자료를 모른다 - 청크가 많은 PDF 하나가 질의와 두루 비슷하면
    상위를 통째로 차지한다. 탄소규제 런(2026-08-14) 실측: 자료 1건이 전체 인용의 28%,
    상위 3건이 52%. 반대편에서 4청크짜리 핵심 자료(K-RE100 이행수단 6가지 조달방식)는
    105~245청크 PDF들에 밀려 20개 절 어디에도 한 번 안 뽑혔다.

    MMR(문장 중복)과는 다른 축이다 - 큰 자료의 청크들은 서로 다른 문단이라 중복
    필터에 안 걸린다. 여기서 막아야 할 것은 '같은 말'이 아니라 '같은 출처'다.

    상한에 걸려 밀린 청크는 버리지 않고 남는 슬롯에 순위대로 되채운다. 자료가 두세
    건뿐인 프로젝트(업로드만 한 경우)에서 상한이 근거를 굶기면 그 절은 못 쓴다 -
    다양성은 고를 게 있을 때만 사는 것이다.
    """
    per_source = max(_MIN_PER_SOURCE, int(cap * settings.retrieval_source_cap_ratio))
    picked: list[int] = []
    deferred: list[int] = []
    used: dict[UUID, int] = {}
    for i, hit in enumerate(ranked):
        if len(picked) >= cap:
            break
        if used.get(hit.source_id, 0) >= per_source:
            deferred.append(i)
            continue
        picked.append(i)
        used[hit.source_id] = used.get(hit.source_id, 0) + 1
    if len(picked) < cap and deferred:
        picked.extend(deferred[: cap - len(picked)])
    # 인덱스로 되돌려 원래 점수 순서를 유지한다(되채운 것이 뒤에 몰리지 않게).
    return [ranked[i] for i in sorted(picked)]


def select_relevant(ranked: list[SearchHit], cap: int) -> list[SearchHit]:
    """리랭커 점수로 '관련 있는 만큼만' 고른다 — 고정 개수가 아니라 분포로 자른다.

    고정 top_k=32는 리랭커 확신 구간을 한참 넘었다(2026-08-12 실측, 14절):
    절당 점수 0.5 이상이 중앙 15개인데 32개를 채우느라 평균 점수가 0.469까지 내려갔다
    (sigmoid에서 0.5가 '반반'이다). 선택된 청크 점수의 하위 10%는 0.011이었다 - 리랭커가
    관련 없다고 판정한 것을 근거랍시고 프롬프트에 실었다는 뜻이다.

    절마다 쓸 만한 근거의 수가 크게 다르다(≥0.5가 최소 0개, 최대 27개). 고정 개수는
    풍부한 절에서 버리고 빈약한 절에서 노이즈로 채운다. 1위 대비 비율로 자르면 절마다
    실제 있는 만큼만 가져간다.

    실측 비교(비율 0.10 + 캡 24 vs 고정 32): 평균 점수 0.469→0.746,
    하위 10% 0.011→0.301, 절당 개수 32→중앙 22.5(최소 4·최대 24).

    최소 _MIN_KEEP개는 남긴다 - 1위 자체가 낮은 절(자료 수집이 비어 있는 절)에서
    근거가 0개가 되면 그 절은 아예 못 쓴다. 그건 검색이 아니라 수집 공백 문제이고,
    design_coverage가 '근거 자료에도 없음'으로 따로 알린다.

    하한을 통과한 것들 중에서 캡을 채울 때는 출처별 상한(_cap_per_source)을 건다 -
    '관련 있는 만큼만'과 '한 자료에 기대지 않기'는 다른 축이고 둘 다 필요하다.
    """
    if not ranked:
        return []
    # 1위마저 0이면 신호가 아예 없다. 비율 하한은 0이 되어 전부 통과해 버리므로,
    # 노이즈를 캡까지 채우지 않도록 최소 개수만 남긴다.
    if ranked[0].score <= 0:
        return ranked[: min(_MIN_KEEP, cap)]
    floor = ranked[0].score * settings.retrieval_score_ratio
    kept = _cap_per_source([h for h in ranked if h.score >= floor], cap)
    return kept or ranked[: min(_MIN_KEEP, cap)]


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
    result = _mmr(rescored, top_k) if settings.mmr_enabled else select_relevant(rescored, top_k)

    logger.info(
        "reranker.rerank_hits.completed",
        n_input=len(hits),
        n_returned=len(result),
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    return result
