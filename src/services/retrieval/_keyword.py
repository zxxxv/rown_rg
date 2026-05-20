"""Keyword (BM25-style) retrieval over chunks via pgroonga.

Module-private: callers route through :class:`HybridSearchClient` in
``hybrid.py``. Direct external use is intentionally not re-exported from
the package ``__init__``.

The query passes through pgroonga's ``&@~`` operator. Default tokenizer
(``TokenBigramSplitSymbolAlphaDigit``-class) handles Korean particle
separation and English abbreviations adequately as verified in
``reports/keyword_search_inspection.md`` §4. Mecab adoption deferred —
see backlog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import text

from src.services.retrieval.base import SearchClient, SearchHit, Track

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger(__name__)


# 파라미터 바인딩으로 SQL injection 방지 — pgroonga 연산자 &@~는 우항을 리터럴로 받지만
# 사용자 입력은 :query placeholder를 통해 asyncpg가 안전하게 escape한다.
_SEARCH_SQL = text(
    """
    SELECT
        id,
        content,
        source_id,
        chunk_index,
        metadata,
        pgroonga_score(tableoid, ctid) AS score
    FROM chunks
    WHERE project_id = :project_id
      AND track = :track
      AND content &@~ :query
    ORDER BY score DESC
    LIMIT :top_k
    """
)


class KeywordSearchClient(SearchClient):
    """pgroonga BM25-style keyword retriever.

    Stateless aside from the session factory. Open a fresh AsyncSession per
    call so the DB connection is not held while the caller fans out into
    other I/O (e.g. semantic search) in the hybrid path.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def search(
        self,
        query: str,
        project_id: UUID,
        track: Track = "content",
        top_k: int = 50,
    ) -> list[SearchHit]:
        # 빈/공백만 있는 쿼리는 pgroonga가 모든 행 매칭 또는 에러로 갈 수 있어 명시적으로 차단.
        if not query.strip():
            logger.info(
                "keyword_search.empty_result",
                project_id=str(project_id),
                reason="empty_query",
            )
            return []

        logger.info(
            "keyword_search.start",
            project_id=str(project_id),
            track=track,
            top_k=top_k,
            query_length=len(query),
        )
        async with self._session_factory() as session:
            result = await session.execute(
                _SEARCH_SQL,
                {
                    "project_id": project_id,
                    "track": track,
                    "query": query,
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
                score=float(row.score),
                score_source="keyword",
            )
            for row in rows
        ]
        logger.info(
            "keyword_search.complete",
            project_id=str(project_id),
            hit_count=len(hits),
        )
        return hits
