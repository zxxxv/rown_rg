from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from src.services.retrieval.base import SearchClient, SearchHit, Track

if TYPE_CHECKING:
    from src.services.retrieval._keyword import KeywordSearchClient
    from src.services.retrieval._semantic import SemanticSearchClient

logger = structlog.get_logger(__name__)


# 외부 top_k 대비 각 백엔드에 더 많이 요청 — RRF 결합 후 잘려나갈 청크에 대한 보완.
# 1.5배는 Cormack 원 논문의 fan-out 권고와 일치하는 범위.
_FAN_OUT_RATIO = 1.5


class HybridSearchClient(SearchClient):
    """Semantic + keyword fusion via Reciprocal Rank Fusion.

    Constructor takes both backends as dependencies — testing can swap in
    mocks without instantiating real DB clients. The RRF constant ``k``
    defaults to 60 (Cormack 2009); tuning is deferred to downstream NDCG
    evaluation.
    """

    def __init__(
        self,
        semantic_client: SemanticSearchClient,
        keyword_client: KeywordSearchClient,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be positive, got {rrf_k}")
        self._semantic = semantic_client
        self._keyword = keyword_client
        self._rrf_k = rrf_k

    async def search(
        self,
        query: str,
        project_id: UUID,
        track: Track = "content",
        top_k: int = 50,
    ) -> list[SearchHit]:
        if not query.strip():
            return []

        fan_top_k = max(top_k, int(top_k * _FAN_OUT_RATIO))
        t0 = time.perf_counter()
        logger.info(
            "hybrid_search.start",
            project_id=str(project_id),
            track=track,
            top_k=top_k,
            fan_top_k=fan_top_k,
        )

        # 두 검색기는 각자 session_factory를 들고 있어 별도 session으로 동시 실행
        semantic_hits, keyword_hits = await asyncio.gather(
            self._semantic.search(query, project_id, track, fan_top_k),
            self._keyword.search(query, project_id, track, fan_top_k),
        )

        fused = self._fuse(semantic_hits, keyword_hits)
        logger.info(
            "hybrid_search.fusion_complete",
            project_id=str(project_id),
            semantic_hits=len(semantic_hits),
            keyword_hits=len(keyword_hits),
            fused_count=len(fused),
        )

        top = fused[:top_k]
        logger.info(
            "hybrid_search.complete",
            project_id=str(project_id),
            hit_count=len(top),
            total_duration_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return top

    def _fuse(
        self, semantic_hits: list[SearchHit], keyword_hits: list[SearchHit]
    ) -> list[SearchHit]:
        """Combine rankings by RRF: ``score = sum(1 / (k + rank + 1))`` over backends."""
        scores: dict[UUID, float] = {}
        # 첫 출현 hit의 메타·content를 그대로 보존 — chunk_id가 같으면 같은 행이라 정합성 OK.
        first_seen: dict[UUID, SearchHit] = {}

        for ranking in (semantic_hits, keyword_hits):
            for rank, hit in enumerate(ranking):
                rrf_score = 1.0 / (self._rrf_k + rank + 1)
                scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + rrf_score
                if hit.chunk_id not in first_seen:
                    first_seen[hit.chunk_id] = hit

        merged = [
            SearchHit(
                chunk_id=chunk_id,
                content=first_seen[chunk_id].content,
                source_id=first_seen[chunk_id].source_id,
                chunk_index=first_seen[chunk_id].chunk_index,
                metadata=first_seen[chunk_id].metadata,
                score=score,
                score_source="hybrid",
            )
            for chunk_id, score in scores.items()
        ]
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged
