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
        """주제 앵커는 재채점 쿼리에만 — 1차 검색은 짧게(dense 벡터 희석 방지).

        절 제목만으로 재채점하면 일반 제목이 주제 무관 청크를 상위로 올린다
        (2026-08-03 주제 표류 실측). 앵커는 채점 단계에서만 결합하되, 변별력 순서로
        뒤에 붙는다 — 주제문은 절마다 같아서 앞에 두면 절 간 차이를 지운다.
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
        # 1차 검색 쿼리는 절 제목 그대로(장 제목이 없는 계획이므로)
        assert client.calls[0][0] == "국내외 시장 규모 및 구조"
        # 재채점 쿼리는 절 제목이 앞, 주제 앵커가 뒤
        assert reranker.calls[0][0] == (
            "국내외 시장 규모 및 구조 — 원격/하이브리드 근무 형태와 조직 내 소통"
        )

    async def test_chapter_title_joins_both_queries(self):
        """장 제목이 있으면 1차 검색·재채점 모두에 실린다.

        비교형 목차에서 '개요'·'시사점' 같은 절 제목은 장마다 반복된다 — 장을 안 실으면
        네 장이 글자까지 같은 질의를 던져 같은 근거를 받는다(2026-08-14 실측:
        1.3과 2.3의 인용 자료 집합이 완전 일치).
        """
        client = _FakeSearchClient([_hit("h0")])
        reranker = _FakeReranker()
        plan = _section("국내 기업 대응수준 진단 및 조사항목 도출").model_copy(
            update={"chapter_title": "EU CBAM"}
        )
        await retrieve_for_section(
            plan,
            client=client,
            project_id=uuid4(),
            top_k=1,
            reranker=reranker,  # type: ignore[arg-type]
            fetch_k=1,
            topic="글로벌 탄소규제의 도입 및 적용 동향",
        )
        assert client.calls[0][0] == "EU CBAM 국내 기업 대응수준 진단 및 조사항목 도출"
        assert reranker.calls[0][0].startswith("EU CBAM 국내 기업 대응수준")

    async def test_long_topic_is_capped_in_rerank_query(self):
        """긴 주제문은 앵커 앞머리만 — 리랭커는 query를 안 자르고 passage만 자른다.

        탄소규제 런의 topic은 383자(189토큰)였고, 512 창에서 근거 본문 몫이
        240~300토큰밖에 안 남았다(청크 중앙값 518자 ≈ 340토큰).
        """
        client = _FakeSearchClient([_hit("h0")])
        reranker = _FakeReranker()
        await retrieve_for_section(
            _section("개요"),
            client=client,
            project_id=uuid4(),
            top_k=1,
            reranker=reranker,  # type: ignore[arg-type]
            fetch_k=1,
            topic="목적: " + "글로벌 탄소규제 동향과 국내 기업 대응 수준 진단 " * 10,
        )
        assert len(reranker.calls[0][0]) <= 240
