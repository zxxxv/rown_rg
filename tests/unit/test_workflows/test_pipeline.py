"""파이프라인 관통 검증 — research→SOURCE_POOL→write→QA_SELECT→assemble→완료 (인메모리).

stages의 플래너·리서치·인덱서·검색기·LLM·익스포터 전역을 fake로 교체해
실검색/실LLM/DB 없이 척추 배선을 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import ProjectStage, RetrievedChunk, ReviewGate, SectionPlan
from src.services.indexing.vector import IndexingResult
from src.services.research import CollectedSource, ResearchResult, ResearchSpec
from src.workflows.pipeline import Done, Paused, advance
from src.workflows.write_loop import apply_selection, rehydrate_from_payload


class _StubClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


@pytest.fixture(autouse=True)
def fake_export(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    """assemble의 HWPX 렌더를 tmp로 돌려 실파일이 exports/에 안 쌓이게. 호출 기록 반환."""
    exported: list[Path] = []

    def _export(state: ProjectState) -> Path:
        path = tmp_path / f"{state.project_id}.hwpx"
        path.write_bytes(b"hwpx")
        exported.append(path)
        return path

    monkeypatch.setattr("src.workflows.stages._exporter", _export)
    return exported


_PLAN_JSON = (
    "```json\n"
    '{"sections": ['
    '{"chapter": 1, "section": 1, "title": "고령화 추이"},'
    '{"chapter": 2, "section": 1, "title": "비용편익 분석"}'
    "]}\n```"
)


class _FakeResearchService:
    """수집 fake — 목차가 spec으로 전달되는지 기록하고 본문 有/無 출처를 돌려준다."""

    def __init__(self) -> None:
        self.specs: list[ResearchSpec] = []

    async def collect(self, spec: ResearchSpec) -> ResearchResult:
        self.specs.append(spec)
        return ResearchResult(
            spec=spec,
            sources=[
                CollectedSource(
                    url="https://example.org/a",
                    title="정부 통계 A",
                    content_md="# 본문\n고령화율은 17.1%다.",
                    reliability="high",
                    matched_sections=spec.outline[:1],
                ),
                CollectedSource(url="https://example.org/b", title="본문 없는 출처 B"),
            ],
            manifest={},
            coverage_gaps=[],
        )


class _FakeIndexer:
    """인덱서 fake — 본문 있는 출처만 청크가 생기는 실동작을 흉내."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def index(
        self,
        *,
        project_id: object,
        content_md: str,
        url: str | None = None,
        title: str | None = None,
        track: str = "content",
        reliability: str | None = None,
    ) -> IndexingResult:
        self.calls.append(content_md)
        return IndexingResult(
            source_id=uuid4(),
            chunks_created=3 if content_md else 0,
            parse_cached=False,
            elapsed_ms=1.0,
        )


@pytest.fixture
def fake_research(monkeypatch: pytest.MonkeyPatch) -> _FakeResearchService:
    """research 스테이지의 플래너 LLM·리서치 서비스·웹 인덱서를 fake로 교체."""
    service = _FakeResearchService()
    monkeypatch.setattr("src.workflows.stages._plan_client", _StubClient(_PLAN_JSON))
    monkeypatch.setattr("src.workflows.stages._research_service_factory", lambda: service)
    monkeypatch.setattr("src.workflows.stages._web_indexer_factory", lambda: _FakeIndexer())
    return service


@pytest.fixture
def fake_write(monkeypatch: pytest.MonkeyPatch) -> RetrievedChunk:
    """write 스테이지의 검색기·LLM을 fake로 교체. 인용 근거 chunk를 돌려준다."""
    chunk = RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content="근거 " * 50, score=0.9)

    async def fake_retrieve(section: SectionPlan) -> list[RetrievedChunk]:
        return [chunk]

    monkeypatch.setattr("src.workflows.stages._retriever_factory", lambda state: fake_retrieve)
    monkeypatch.setattr(
        "src.workflows.stages._write_client", _StubClient("이 섹션 본문입니다. [1] " * 30)
    )
    return chunk


def _state_at_research() -> ProjectState:
    return ProjectState(
        user_id=uuid4(),
        topic="주제",
        section_plan=[
            SectionPlan(chapter_number=1, section_number=1, title="개요"),
            SectionPlan(chapter_number=2, section_number=1, title="분석"),
        ],
        current_stage=ProjectStage.RESEARCHING,
    )


class TestResearchPausesAtSourcePool:
    async def test_research_plans_collects_indexes(self, fake_research: _FakeResearchService):
        state = ProjectState(user_id=uuid4(), topic="인구 고령화 대응")  # CREATED
        outcome = await advance(state)

        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.SOURCE_POOL
        assert outcome.state.current_stage is ProjectStage.RESEARCHING
        # 플래너가 만든 목차가 수집 spec으로 전달됨
        assert fake_research.specs[0].outline == ["고령화 추이", "비용편익 분석"]
        # 게이트 payload에 목차+자료 풀이 함께 실림 (resume 복원원)
        assert len(outcome.review.payload["section_plan"]) == 2
        assert len(outcome.review.payload["sources"]) == 2
        # 자료 2건 모두 풀에 남고, 본문 있는 1건만 인덱싱됨
        assert len(outcome.state.sources) == 2
        assert len(outcome.state.indexed_source_ids) == 1


class TestFullPipelineChain:
    async def test_created_to_completed(
        self,
        fake_research: _FakeResearchService,
        fake_write: RetrievedChunk,
        fake_export: list[Path],
    ):
        # 1) research → SOURCE_POOL 정지
        paused_sources = await advance(ProjectState(user_id=uuid4(), topic="주제"))
        assert isinstance(paused_sources, Paused)
        assert paused_sources.review.gate is ReviewGate.SOURCE_POOL

        # 2) 승인 후 재개 — write는 research가 만든 목차를 그대로 쓴다
        resumed = paused_sources.state.resolve_review(paused_sources.review)
        paused_qa = await advance(resumed)
        assert isinstance(paused_qa, Paused)
        assert paused_qa.review.gate is ReviewGate.QA_SELECT
        plan_ids = {str(s.section_id) for s in paused_sources.state.section_plan}
        assert {sec["section_id"] for sec in paused_qa.review.payload["sections"]} == plan_ids

        # 3) 사람이 후보 선택 → assemble → 완료 + HWPX 렌더 1회
        selections = {
            sec["section_id"]: sec["candidates"][0]["candidate_id"]
            for sec in paused_qa.review.payload["sections"]
        }
        final_state = apply_selection(paused_qa.state.resolve_review(paused_qa.review), selections)
        done = await advance(final_state)
        assert isinstance(done, Done)
        assert done.state.current_stage is ProjectStage.COMPLETED
        assert len(fake_export) == 1


class TestWritePausesAtQaSelect:
    async def test_write_produces_candidates_and_pauses(self, fake_write: RetrievedChunk):
        outcome = await advance(_state_at_research())
        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.QA_SELECT
        assert outcome.state.current_stage is ProjectStage.REVIEWING
        sections = outcome.review.payload["sections"]
        assert len(sections) == 2
        # 각 섹션에 survivors 존재 (게이트 통과)
        for sec in sections:
            assert len(sec["candidates"]) == 2
            assert sec["all_excluded"] is False


class TestResumeThroughAssemble:
    async def test_full_round_trip_completes(self, fake_write: RetrievedChunk):
        # 1) write → QA_SELECT 정지
        paused = await advance(_state_at_research())
        assert isinstance(paused, Paused)
        payload = paused.review.payload

        # 2) 사람이 각 섹션 첫 후보 선택 (decision)
        selections = {
            sec["section_id"]: sec["candidates"][0]["candidate_id"] for sec in payload["sections"]
        }

        # 3) resume — 새 프로세스처럼 빈 state에서 payload로 재수화 + 선택 반영
        fresh = ProjectState(
            user_id=paused.state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)

        # 4) assemble → 완료
        done = await advance(fresh)
        assert isinstance(done, Done)
        assert done.state.current_stage is ProjectStage.COMPLETED
        assert len(done.state.selected_drafts()) == 2

    async def test_missing_selection_still_completes_but_incomplete_structure(
        self, fake_write: RetrievedChunk
    ):
        # 한 섹션만 선택 → assemble은 진행하되 selected_drafts는 1개 (structure 미완)
        paused = await advance(_state_at_research())
        assert isinstance(paused, Paused)
        payload = paused.review.payload
        first = payload["sections"][0]
        selections = {first["section_id"]: first["candidates"][0]["candidate_id"]}

        fresh = ProjectState(
            user_id=paused.state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)
        done = await advance(fresh)
        assert isinstance(done, Done)
        assert len(done.state.selected_drafts()) == 1
