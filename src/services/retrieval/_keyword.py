"""Keyword (BM25-style) retrieval over chunks via pgroonga.

Module-private: callers route through :class:`HybridSearchClient` in
``hybrid.py``. Direct external use is intentionally not re-exported from
the package ``__init__``.

The query is rewritten to pgroonga OR form (``to_or_query``) before hitting
the ``&@~`` operator - space means AND there, so a multi-word section title
matched almost nothing. Ranking is left to ``pgroonga_score``.

The query passes through pgroonga's ``&@~`` operator. Default tokenizer
(``TokenBigramSplitSymbolAlphaDigit``-class) handles Korean particle
separation and English abbreviations adequately as verified in
``reports/keyword_search_inspection.md`` §4. Mecab adoption deferred —
see backlog.
"""

from __future__ import annotations

import re
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
    -- 근거로 못 쓰는 청크(사이트 메뉴·그림 껍데기·중복 등)는 검색에서 뺀다.
    -- 지우지 않고 표시만 하므로 원문 대조 화면에는 그대로 남는다.
      AND coalesce(metadata->>'excluded', '') = ''
      AND content &@~ :query
      AND source_id NOT IN (
          SELECT id FROM project_sources
          WHERE project_id = :project_id AND is_included = false
      )
    ORDER BY score DESC
    LIMIT :top_k
    """
)


# pgroonga 질의 문법에서 공백은 AND다. 절 제목을 그대로 넣으면 제목의 모든 단어가
# 한 청크 안에 다 들어 있어야 매칭돼 사실상 아무것도 안 걸린다(2026-08-10 실측:
# 예타 런 12절 중 10절이 0건 → 하이브리드의 키워드 절반이 통째로 죽어 있었다).
# 토큰을 OR로 묶고 순위는 pgroonga_score에 맡긴다 — 더 많은 항이 걸린 청크가 위로 온다.
_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{2,}")
# 어느 문서에나 있어 변별력이 없는 접속·수식어. AND일 땐 매칭을 막고 OR일 땐 잡음이다.
_STOPWORDS = frozenset({"및", "등", "관련", "위한", "대한", "중심", "기반", "그리고", "통한"})


def to_or_query(query: str) -> str:
    """자연어 질의 → pgroonga OR 질의. 토큰이 없으면 원문을 그대로 돌려준다.

    특수문자는 토큰 정규식에서 떨어져 나가므로 질의 문법 주입(따옴표·괄호 등)도
    함께 차단된다.
    """
    tokens = [t for t in _TOKEN_RE.findall(query) if t not in _STOPWORDS]
    return " OR ".join(tokens) if tokens else query


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
        or_query = to_or_query(query)
        async with self._session_factory() as session:
            result = await session.execute(
                _SEARCH_SQL,
                {
                    "project_id": project_id,
                    "track": track,
                    "query": or_query,
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
