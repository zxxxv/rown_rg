"""섹션 단위 검색 어댑터 — retrieval 백엔드(SearchHit)를 core RetrievedChunk로 변환.

write 루프는 SectionPlan 하나를 받아 근거 청크를 돌려주는 SectionRetriever만 알면
된다(실검색이든 테스트 주입이든). make_section_retriever로 프로젝트·검색기에 바인딩한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from src.core.types import RetrievedChunk, SectionPlan
from src.services.retrieval.base import SearchClient, SearchHit, Track

# 섹션 하나 → 근거 청크. write 루프가 의존하는 유일한 검색 인터페이스.
SectionRetriever = Callable[[SectionPlan], Awaitable[list[RetrievedChunk]]]

DEFAULT_TOP_K = 10


def hit_to_chunk(hit: SearchHit) -> RetrievedChunk:
    """검색 백엔드 SearchHit을 생성·게이트가 쓰는 RetrievedChunk로 축약."""
    return RetrievedChunk(
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        content=hit.content,
        score=hit.score,
    )


def _section_query(section: SectionPlan) -> str:
    """섹션 검색 쿼리 — 지금은 제목. 추후 챕터 맥락·키워드로 확장 가능."""
    return section.title


async def retrieve_for_section(
    section: SectionPlan,
    *,
    client: SearchClient,
    project_id: UUID,
    track: Track = "content",
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedChunk]:
    """한 섹션의 근거 청크를 검색해 RetrievedChunk 리스트로 반환."""
    hits = await client.search(_section_query(section), project_id, track, top_k)
    return [hit_to_chunk(h) for h in hits]


def make_section_retriever(
    client: SearchClient,
    project_id: UUID,
    *,
    track: Track = "content",
    top_k: int = DEFAULT_TOP_K,
) -> SectionRetriever:
    """프로젝트·검색기에 바인딩된 SectionRetriever를 만든다 (write 루프 주입용)."""

    async def _retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        return await retrieve_for_section(
            section, client=client, project_id=project_id, track=track, top_k=top_k
        )

    return _retrieve
