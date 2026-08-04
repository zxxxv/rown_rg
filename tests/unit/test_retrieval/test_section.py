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


class _FakeReranker:
    """score_pairs만 흉내 — 입력 역순으로 높은 점수를 줘 재정렬을 검증한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, list(passages)))
        return [i * 0.1 for i in range(len(passages))]


class TestRerankerPath:
    async def test_wide_fetch_rerank_then_truncate(self):
        hits = [_hit(f"h{i}") for i in range(5)]
        client = _FakeSearchClient(hits)
        reranker = _FakeReranker()
        chunks = await retrieve_for_section(
            _section("경제성 분석"),
            client=client,
            project_id=uuid4(),
            top_k=2,
            reranker=reranker,  # type: ignore[arg-type]
            fetch_k=5,
        )
        # 1차 검색은 fetch_k 폭으로 넓게
        assert client.calls[0][3] == 5
        # 재채점(역순 점수) 상위 2개로 축소·재정렬
        assert [c.content for c in chunks] == ["h4", "h3"]
        # topic 미지정이면 재채점 질의는 절 제목 그대로
        assert reranker.calls[0][0] == "경제성 분석"
        # 원본 점수는 audit 가능해야 하므로 score가 리랭커 점수로 교체됨
        assert chunks[0].score == 0.4

    async def test_fetch_k_never_below_top_k(self):
        client = _FakeSearchClient([])
        reranker = _FakeReranker()
        await retrieve_for_section(
            _section(),
            client=client,
            project_id=uuid4(),
            top_k=40,
            reranker=reranker,  # type: ignore[arg-type]
            fetch_k=30,
        )
        assert client.calls[0][3] == 40

    async def test_no_reranker_keeps_narrow_search(self):
        client = _FakeSearchClient([])
        await retrieve_for_section(_section(), client=client, project_id=uuid4(), top_k=10)
        assert client.calls[0][3] == 10

    async def test_topic_anchor_applies_to_rerank_not_recall(self):
        """주제 앵커는 재채점 쿼리에만 — 1차 검색(pgroonga AND)은 절 제목 유지.

        절 제목만으로 재채점하면 일반 제목이 주제 무관 청크를 상위로 올린다
        (2026-08-03 주제 표류 실측). 앵커는 채점 단계에서만 결합한다.
        """
        hits = [_hit("h0")]
        client = _FakeSearchClient(hits)
        reranker = _FakeReranker()
        await retrieve_for_section(
            _section("국내외 시장 규모 및 구조"),
            client=client,
            project_id=uuid4(),
            top_k=1,
            reranker=reranker,  # type: ignore[arg-type]
            fetch_k=1,
            topic="원격/하이브리드 근무 형태와 조직 내 소통",
        )
        # 1차 검색 쿼리는 절 제목 그대로(재현율 보호)
        assert client.calls[0][0] == "국내외 시장 규모 및 구조"
        # 재채점 쿼리에는 주제가 앞에 결합된다
        assert reranker.calls[0][0] == (
            "원격/하이브리드 근무 형태와 조직 내 소통 — 국내외 시장 규모 및 구조"
        )
