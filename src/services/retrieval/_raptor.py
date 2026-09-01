"""RAPTOR 요약 노드 검색 — 섹션 검색에 곁들일 '배경 맥락' 공급자.

모듈-프라이빗: retrieve_for_section의 summary_fetcher로만 쓰인다. 요약 노드는
is_summary=True로 표시되어 후보 생성 프롬프트에서 인용 불가 맥락 블록으로 분리된다
(인용은 leaf 청크만 — 참고문헌 무결성 유지).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import text

from src.core.types import RetrievedChunk
from src.services.retrieval._semantic import _vec_literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.clients.embedding_client import EmbeddingClient

logger = structlog.get_logger(__name__)

SummaryFetcher = Callable[[str], Awaitable[list[RetrievedChunk]]]

_SEARCH_SQL = text(
    """
    SELECT
        id,
        summary,
        (embedding <=> CAST(:query_vec AS vector)) AS distance
    FROM raptor_nodes
    WHERE project_id = :project_id
      AND summary IS NOT NULL
    ORDER BY embedding <=> CAST(:query_vec AS vector)
    LIMIT :top_k
    """
)


def make_summary_fetcher(
    session_maker: async_sessionmaker[AsyncSession],
    embedder: EmbeddingClient,
    project_id: UUID,
    *,
    top_k: int,
    min_similarity: float = 0.0,
) -> SummaryFetcher:
    """프로젝트에 바인딩된 요약 노드 검색기 — 트리가 없으면 조용히 빈 결과.

    min_similarity: 코사인 유사도 하한 — top_k 고정 채우기는 관련 없는 요약도 억지로
    실었다(캡 12 시절 유해 실측의 잔재 경로). 하한을 넘는 것만 0~top_k개 가변 채택.
    """

    async def fetch(query: str) -> list[RetrievedChunk]:
        vec = (await embedder.embed(query)).embedding
        async with session_maker() as session:
            rows = (
                await session.execute(
                    _SEARCH_SQL,
                    {
                        "query_vec": _vec_literal(list(vec)),
                        "project_id": str(project_id),
                        "top_k": top_k,
                    },
                )
            ).all()
        return [
            RetrievedChunk(
                chunk_id=row.id,
                source_id=row.id,
                content=row.summary,
                score=score,
                is_summary=True,
            )
            for row in rows
            if (score := max(0.0, 1.0 - float(row.distance))) >= min_similarity
        ]

    return fetch
