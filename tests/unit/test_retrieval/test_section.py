"""섹션 검색 어댑터 검증 — fake SearchClient로 DB 없이 SearchHit→RetrievedChunk 매핑 확인."""

from __future__ import annotations

from uuid import UUID, uuid4

from src.core.types import RetrievedChunk, SectionPlan
from src.services.retrieval.base import SearchHit, Track
from src.services.retrieval.section import (
    hit_to_chunk,
    make_section_retriever,
    retrieve_for_section,
)


class _FakeSearchClient:
    """search 호출 인자를 기록하고 준비된 hit을 돌려주는 가짜 검색기."""

    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.calls: list[tuple[str, UUID, Track, int]] = []

    async def search(
        self, query: str, project_id: UUID, track: Track = "content", top_k: int = 50
    ) -> list[SearchHit]:
        self.calls.append((query, project_id, track, top_k))
        return self._hits


def _hit(content: str = "근거") -> SearchHit:
    return SearchHit(
        chunk_id=uuid4(),
        content=content,
        source_id=uuid4(),
        chunk_index=0,
        score=0.5,
        score_source="hybrid",
    )


def _section(title: str = "분석") -> SectionPlan:
    return SectionPlan(chapter_number=1, section_number=2, title=title)


class TestHitToChunk:
    def test_maps_core_fields_drops_backend_only(self):
        hit = _hit("본문")
        chunk = hit_to_chunk(hit)
        assert isinstance(chunk, RetrievedChunk)
        assert chunk.chunk_id == hit.chunk_id
        assert chunk.source_id == hit.source_id
        assert chunk.content == "본문"
        assert chunk.score == hit.score


class TestRetrieveForSection:
    async def test_returns_retrieved_chunks(self):
        hits = [_hit("a"), _hit("b")]
        client = _FakeSearchClient(hits)
        chunks = await retrieve_for_section(_section(), client=client, project_id=uuid4())
        assert [c.chunk_id for c in chunks] == [h.chunk_id for h in hits]
        assert all(isinstance(c, RetrievedChunk) for c in chunks)

    async def test_query_is_section_title(self):
        client = _FakeSearchClient([])
        pid = uuid4()
        await retrieve_for_section(_section("경제성 분석"), client=client, project_id=pid, top_k=7)
        query, project_id, track, top_k = client.calls[0]
        assert query == "경제성 분석"
        assert project_id == pid
        assert track == "content"
        assert top_k == 7


class TestMakeSectionRetriever:
    async def test_binds_project_and_client(self):
        hits = [_hit()]
        client = _FakeSearchClient(hits)
        pid = uuid4()
        retriever = make_section_retriever(client, pid, top_k=3)
        chunks = await retriever(_section())
        assert [c.chunk_id for c in chunks] == [h.chunk_id for h in hits]
        assert client.calls[0][1] == pid
        assert client.calls[0][3] == 3
