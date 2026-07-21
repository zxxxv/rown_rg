"""섹션 단위 검색 어댑터 — retrieval 백엔드(SearchHit)를 core RetrievedChunk로 변환.

write 루프는 SectionPlan 하나를 받아 근거 청크를 돌려주는 SectionRetriever만 알면
된다(실검색이든 테스트 주입이든). make_section_retriever로 프로젝트·검색기에 바인딩한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from src.core.types import RetrievedChunk, SectionPlan
from src.services.retrieval.base import SearchClient, SearchHit, Track

if TYPE_CHECKING:
    from src.clients.reranker_client import RerankerClient

# 섹션 하나 → 근거 청크. write 루프가 의존하는 유일한 검색 인터페이스.
SectionRetriever = Callable[[SectionPlan], Awaitable[list[RetrievedChunk]]]

DEFAULT_TOP_K = 10
# 리랭커 사용 시 1차 검색 폭 — 넓게 가져와 cross-encoder가 top_k로 추리게 한다.
DEFAULT_FETCH_K = 30


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
    reranker: RerankerClient | None = None,
    fetch_k: int = DEFAULT_FETCH_K,
) -> list[RetrievedChunk]:
    """한 섹션의 근거 청크를 검색해 RetrievedChunk 리스트로 반환.

    reranker가 주어지면 넓게(fetch_k) 검색한 뒤 cross-encoder로 재채점해 top_k로
    줄인다. 재채점 질의는 항상 원 쿼리다 — HyDE 확장은 semantic 백엔드 내부에만
    적용되므로 여기서는 보이지 않는다.
    """
    query = _section_query(section)
    if reranker is None:
        hits = await client.search(query, project_id, track, top_k)
    else:
        # lazy import: _reranking → reranker_client 체인의 무거운 의존을 사용 시점으로 미룬다.
        from src.services.retrieval._reranking import rerank_hits

        wide = await client.search(query, project_id, track, max(fetch_k, top_k))
        hits = await rerank_hits(reranker, query, wide, top_k=top_k)
    return [hit_to_chunk(h) for h in hits]


def make_section_retriever(
    client: SearchClient,
    project_id: UUID,
    *,
    track: Track = "content",
    top_k: int = DEFAULT_TOP_K,
    reranker: RerankerClient | None = None,
    fetch_k: int = DEFAULT_FETCH_K,
) -> SectionRetriever:
    """프로젝트·검색기에 바인딩된 SectionRetriever를 만든다 (write 루프 주입용)."""

    async def _retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        return await retrieve_for_section(
            section,
            client=client,
            project_id=project_id,
            track=track,
            top_k=top_k,
            reranker=reranker,
            fetch_k=fetch_k,
        )

    return _retrieve
