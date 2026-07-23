"""Semantic (vector) retrieval over chunks via pgvector + BGE-M3.

Module-private: callers route through :class:`HybridSearchClient`. Direct
external use is intentionally not re-exported from the package ``__init__``.

The query goes through ``BgeM3Client.embed`` (L2-normalized, 1024-dim) and
the SQL uses pgvector's cosine distance operator ``<=>`` aligned with the
``vector_cosine_ops`` index class on ``chunks.embedding`` (마이그 0007).
``score = 1 - distance`` reconstructs cosine similarity (정규화 임베딩 가정).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import text

from src.services.retrieval.base import SearchClient, SearchHit, Track

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.clients.embedding_client import EmbeddingClient

logger = structlog.get_logger(__name__)


# pgvector 0.8.2 기준 — ef_search는 SET LOCAL로 트랜잭션 스코프 적용. 100은 외부 top_k=50의
# fan-out 1.5배(=75)에 대해 ef >= 2*top_k 권장값을 만족하는 최소치.
_EF_SEARCH = 100

# asyncpg는 vector 커스텀 타입을 직접 인지하지 못해 list[float] 바인딩이 실패한다.
# pgvector 텍스트 형식 '[1.0,2.0,...]'으로 변환 후 ::vector 캐스트로 우회.
_SEARCH_SQL = text(
    """
    SELECT
        id,
        content,
        source_id,
        chunk_index,
        metadata,
        (embedding <=> CAST(:query_vec AS vector)) AS distance
    FROM chunks
    WHERE project_id = :project_id
      AND track = :track
      AND source_id NOT IN (
          SELECT id FROM project_sources
          WHERE project_id = :project_id AND is_included = false
      )
    ORDER BY embedding <=> CAST(:query_vec AS vector)
    LIMIT :top_k
    """
)


def _vec_literal(v: list[float]) -> str:
    """Format list[float] as pgvector text literal (e.g. ``'[0.1,0.2,...]'``)."""
    return "[" + ",".join(repr(x) for x in v) + "]"


class SemanticSearchClient(SearchClient):
    """pgvector cosine semantic retriever.

    Builds one query embedding per ``search`` call, then runs an HNSW
    cosine-distance scan. Each call opens a fresh AsyncSession so the
    hybrid path's ``asyncio.gather`` does not serialize on a shared
    session.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: EmbeddingClient,
        query_expander: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        # HyDE 등 선택적 쿼리 확장 — dense 검색에만 적용된다(keyword는 원 쿼리 유지).
        self._query_expander = query_expander

    async def search(
        self,
        query: str,
        project_id: UUID,
        track: Track = "content",
        top_k: int = 50,
    ) -> list[SearchHit]:
        if not query.strip():
            logger.info(
                "semantic_search.empty_embedding",
                project_id=str(project_id),
                reason="empty_query",
            )
            return []

        logger.info(
            "semantic_search.start",
            project_id=str(project_id),
            track=track,
            top_k=top_k,
            query_length=len(query),
        )

        dense_query = query
        if self._query_expander is not None:
            dense_query = await self._query_expander(query)
            if dense_query != query:
                logger.info(
                    "semantic_search.query_expanded",
                    project_id=str(project_id),
                    original_length=len(query),
                    expanded_length=len(dense_query),
                )

        embed_result = await self._embedder.embed(dense_query)
        query_vec = _vec_literal(embed_result.embedding)

        async with self._session_factory() as session:
            async with session.begin():
                # SET LOCAL은 begin() 안의 트랜잭션 스코프에서만 유효 — 다른 쿼리 영향 X.
                await session.execute(text(f"SET LOCAL hnsw.ef_search = {_EF_SEARCH}"))
                result = await session.execute(
                    _SEARCH_SQL,
                    {
                        "project_id": project_id,
                        "track": track,
                        "query_vec": query_vec,
                        "top_k": top_k,
                    },
                )
                rows = result.all()

        hits = [
            SearchHit(
                chunk_id=row.id,
                content=row.content,
                source_id=row.source_id,
                chunk_index=row.chunk_index,
                metadata=row.metadata or {},
                # 정규화 임베딩 가정에서 코사인 유사도 = 1 - distance.
                score=1.0 - float(row.distance),
                score_source="semantic",
            )
            for row in rows
        ]
        logger.info(
            "semantic_search.complete",
            project_id=str(project_id),
            hit_count=len(hits),
        )
        return hits
