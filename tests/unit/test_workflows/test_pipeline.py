"""파이프라인 관통 검증 — research→SOURCE_POOL→index→write(자동 채택)→assemble→완료.

stages의 플래너·리서치·인덱서·검색기·LLM·익스포터 전역을 fake로 교체해
실검색/실LLM/DB 없이 척추 배선을 검증한다. QA 게이트는 제거됨(2026-08-07) —
레거시 pending 게이트의 payload 재수화 경로만 별도 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.clients.llm.exceptions import LLMAPIError
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import (
    ProjectStage,
    RetrievedChunk,
    ReviewGate,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
)
from src.services.indexing.vector import IndexingResult
from src.services.indexing.web import StagedWebSource
from src.services.research import CollectedSource, ResearchResult, ResearchSpec
from src.workflows.pipeline import Done, Paused, advance
from src.workflows.write_loop import apply_selection, qa_select_payload, rehydrate_from_payload


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

    def _export(state: ProjectState, glossary: dict | None = None) -> Path:
        path = tmp_path / f"{state.project_id}.hwpx"
        path.write_bytes(b"hwpx")
        exported.append(path)
        return path

    async def _no_store(_state: ProjectState) -> None:
        # 인메모리 척추 검증 — 섹션 영구저장(DB)은 건너뛴다.
        return None

    async def _no_draft_store(_state: ProjectState, _plan: object, _draft: object) -> None:
        return None

    async def _no_cleaner(_project_id: object) -> None:
        return None

    async def _no_working_copy(_project_id: object) -> dict:
        # 인메모리 척추 검증 — DB 작업 사본 없음(편집 안 함과 동일).
        return {}

    monkeypatch.setattr("src.workflows.stages._exporter", _export)
    monkeypatch.setattr("src.workflows.stages._section_store", _no_store)
    monkeypatch.setattr("src.workflows.stages._draft_store", _no_draft_store)
    monkeypatch.setattr("src.workflows.stages._sections_cleaner", _no_cleaner)
    monkeypatch.setattr("src.workflows.stages._working_copy", _no_working_copy)
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

    async def collect(self, spec: ResearchSpec, **_kwargs: object) -> ResearchResult:
        self.specs.append(spec)
        return ResearchResult(
            spec=spec,
            sources=[
                CollectedSource(
                    url="https://example.org/a",
                    title="정부 통계 A",
                    # 본문 판정 하한(_MIN_CONTENT_CHARS=200자)을 넘는 실본문 모사
                    content_md="# 본문\n고령화율은 17.1%다. "
                    + "노인 부양비가 상승하고 생산가능인구는 감소한다. " * 10,
                    reliability="high",
                    matched_sections=spec.outline[:1],
                ),
                CollectedSource(url="https://example.org/b", title="본문 없는 출처 B"),
            ],
            manifest={},
            coverage_gaps=[],
        )


class _FakeIndexer:
    """스테이지→색인 fake. collect가 stage로 저장한 것을 index가 load_included로 읽는다.

    확정 게이트 전(stage)엔 임베딩하지 않고, 게이트 뒤(index_existing)에서 본문 있는
    출처만 청크가 생기는 실동작을 흉내. 한 인스턴스를 공유해 stage/load가 상태를 나눈다.
    """

    def __init__(self) -> None:
        self.staged: list[tuple[UUID, str]] = []

    async def stage(
        self,
        *,
        project_id: object,
        content_md: str,
        url: str | None = None,
        title: str | None = None,
        reliability: str | None = None,
        matched_sections: list[str] | None = None,
        page_age: str | None = None,
    ) -> UUID:
        source_id = uuid4()
        self.staged.append((source_id, content_md))
        return source_id

    async def load_included(self, project_id: object) -> list[StagedWebSource]:
        # 실제 구현은 is_included=true만 읽지만, fake는 DB 없이 전량 반환(제외 없음 happy-path).
        return [StagedWebSource(source_id=sid, content_md=cm) for sid, cm in self.staged]

    async def index_existing(
        self,
        *,
        project_id: object,
        source_id: UUID,
        content_md: str,
        track: str = "content",
    ) -> IndexingResult:
        return IndexingResult(
            source_id=source_id,
            chunks_created=3 if (content_md and content_md.strip()) else 0,
            parse_cached=False,
            elapsed_ms=1.0,
        )


@pytest.fixture
def fake_research(monkeypatch: pytest.MonkeyPatch) -> _FakeResearchService:
    """research 스테이지의 플래너 LLM·리서치 서비스·웹 인덱서를 fake로 교체.

    인덱서는 단일 인스턴스를 공유한다 — collect의 stage와 index의 load_included가
    같은 저장소를 봐야 확정 후 색인이 이어진다.
    """
    service = _FakeResearchService()
    indexer = _FakeIndexer()
    monkeypatch.setattr("src.workflows.stages._plan_client", _StubClient(_PLAN_JSON))
    monkeypatch.setattr("src.workflows.stages._research_service_factory", lambda: service)
    monkeypatch.setattr("src.workflows.stages._web_indexer_factory", lambda: indexer)
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


def _state_at_indexing() -> ProjectState:
    """확정 게이트 직후(INDEXING) 상태 — write 단독 검증용(index는 이 앞 구간)."""
    return ProjectState(
        user_id=uuid4(),
        topic="주제",
        section_plan=[
            SectionPlan(chapter_number=1, section_number=1, title="개요"),
            SectionPlan(chapter_number=2, section_number=1, title="분석"),
        ],
        current_stage=ProjectStage.INDEXING,
    )


class TestResearchPausesAtSourcePool:
    async def test_collect_plans_and_stages_before_gate(self, fake_research: _FakeResearchService):
        state = ProjectState(user_id=uuid4(), topic="인구 고령화 대응")  # CREATED
        outcome = await advance(state)

        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.SOURCE_POOL
        assert outcome.state.current_stage is ProjectStage.RESEARCHING
        # 챕터 단위 분할 수집 — 1차 패스는 챕터마다 해당 절 제목만 spec으로 전달.
        # fake가 매번 같은 URL 2건이라 목표(research_min_sources=20) 미달 →
        # 보충 패스가 정확히 1회 더 돌고, 같은 출처는 전부 중복 제거된다(총 2건 유지).
        outlines = [s.outline for s in fake_research.specs]
        assert outlines == [["고령화 추이"], ["비용편익 분석"]] * 2
        assert all("—" in s.topic for s in fake_research.specs)  # topic에 챕터 라벨 결합
        assert all("추가 심화" in s.topic for s in fake_research.specs[2:])  # 보충 패스 질의 변형
        # 게이트 payload에 목차+자료 풀이 함께 실림 (resume 복원원).
        # 본문 없는 출처 B는 풀에 실리지 않는다(2026-08-03 정책: 껍데기 미스테이징).
        assert len(outcome.review.payload["section_plan"]) == 2
        assert len(outcome.review.payload["sources"]) == 1
        # 게이트 시점엔 아직 임베딩 전 — 스테이징만 됨.
        # (색인은 확정 뒤 index 단계에서 채택된 자료만 수행)
        assert len(outcome.state.sources) == 1
        assert len(outcome.state.indexed_source_ids) == 0

    async def test_chapter_failure_is_isolated(self, monkeypatch: pytest.MonkeyPatch):
        """챕터 하나의 수집 실패(prompt too long 등)가 실행 전체를 죽이지 않는다."""

        class _Flaky(_FakeResearchService):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            async def collect(self, spec: ResearchSpec, **kwargs: object) -> ResearchResult:
                self.calls += 1
                if self.calls == 1:
                    raise LLMAPIError("prompt is too long (모의)")
                return await super().collect(spec, **kwargs)

        service = _Flaky()
        monkeypatch.setattr("src.workflows.stages._plan_client", _StubClient(_PLAN_JSON))
        monkeypatch.setattr("src.workflows.stages._research_service_factory", lambda: service)
        monkeypatch.setattr("src.workflows.stages._web_indexer_factory", lambda: _FakeIndexer())

        outcome = await advance(ProjectState(user_id=uuid4(), topic="인구 고령화 대응"))
        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.SOURCE_POOL
        # 1장(첫 콜) 실패에도 2장 자료(본문 있는 1건)로 게이트가 열린다
        assert len(outcome.state.sources) == 1

    async def test_all_chapters_failed_raises(self, monkeypatch: pytest.MonkeyPatch):
        """성공한 수집 콜이 0이면(키·네트워크 등 시스템 문제) 빈 게이트 대신 실행 실패."""

        class _AlwaysFail(_FakeResearchService):
            async def collect(self, spec: ResearchSpec, **kwargs: object) -> ResearchResult:
                raise LLMAPIError("api down (모의)")

        monkeypatch.setattr("src.workflows.stages._plan_client", _StubClient(_PLAN_JSON))
        monkeypatch.setattr("src.workflows.stages._research_service_factory", _AlwaysFail)
        monkeypatch.setattr("src.workflows.stages._web_indexer_factory", lambda: _FakeIndexer())

        with pytest.raises(LLMAPIError):
            await advance(ProjectState(user_id=uuid4(), topic="인구 고령화 대응"))

    async def test_gate_payload_carries_source_signals(self, fake_research: _FakeResearchService):
        """자료 확정 게이트 payload에 사람이 취사선택할 신호가 실려 나온다."""
        outcome = await advance(ProjectState(user_id=uuid4(), topic="인구 고령화 대응"))
        assert isinstance(outcome, Paused)
        by_url = {s["url"]: s for s in outcome.review.payload["sources"]}

        # 본문 있는 고신뢰 출처: 신뢰도·매칭섹션·미리보기·색인여부가 모두 전달됨
        a = by_url["https://example.org/a"]
        assert a["reliability"] == "high"
        assert a["matched_sections"] == ["고령화 추이"]
        assert a["has_content"] is True
        assert a["preview"] and "17.1%" in a["preview"]

        # 본문 없는 출처 B는 풀에 아예 실리지 않는다(껍데기 미스테이징 정책)
        assert "https://example.org/b" not in by_url

        # 절별 커버리지 — 매칭 자료 0건인 절이 "N.N 제목"으로 표면화된다(추가 검색 신호).
        # 출처 A가 "고령화 추이"에만 매칭됐으므로 "비용편익 분석"이 미커버로 잡힌다.
        coverage = outcome.review.payload["coverage"]
        assert not any("고령화 추이" in u for u in coverage["uncovered_sections"])
        assert any("비용편익 분석" in u for u in coverage["uncovered_sections"])


class TestStageDisplayHook:
    async def test_on_stage_reports_running_phase(self, fake_write: RetrievedChunk):
        """advance 훅이 '지금 실행 중인 단계'를 알린다 — 표시용 상태 영속화의 근거.

        INDEXING에서 출발하면 write가 도는 동안 WRITING이 통지돼야 한다(척추가
        구간 끝에만 상태를 저장해 UI가 옛 위치를 가리키던 문제의 해법).
        """
        seen: list[ProjectStage] = []

        async def hook(stage: ProjectStage) -> None:
            seen.append(stage)

        outcome = await advance(_state_at_indexing(), on_stage=hook)
        # QA 게이트 제거 — write 뒤 정지 없이 assemble(REVIEWING 표시)까지 이어진다
        assert isinstance(outcome, Done)
        assert seen == [ProjectStage.WRITING, ProjectStage.REVIEWING]


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

        # 2) 승인 후 재개 — index(임베딩)→write(자동 채택)→assemble까지 정지 없이 완주
        #    (QA 게이트 제거, 2026-08-07 — 검토는 완성 후 통합 화면에서)
        resumed = paused_sources.state.resolve_review(paused_sources.review)
        done = await advance(resumed)
        assert isinstance(done, Done)
        assert done.state.current_stage is ProjectStage.COMPLETED
        # 확정 이후 index 단계에서 본문 있는 1건이 임베딩됨(게이트 전엔 0이었음)
        assert len(done.state.indexed_source_ids) == 1
        # 자동 채택 — 모든 절이 선택돼 조립됐고 HWPX 렌더 1회
        assert len(done.state.selected_drafts()) == 2
        assert len(fake_export) == 1


class TestWriteAutoSelectsAndAssembles:
    async def test_write_runs_straight_to_completed(
        self, fake_write: RetrievedChunk, fake_export: list[Path]
    ):
        outcome = await advance(_state_at_indexing())
        assert isinstance(outcome, Done)
        assert outcome.state.current_stage is ProjectStage.COMPLETED
        assert len(outcome.state.section_candidates) == 2
        # 각 섹션에 survivors 존재 + 첫 생존 후보 자동 채택. 후보 수는
        # settings.write_candidates_n을 따른다 — 기본은 1(2026-08-07 n=1 확정).
        for cset in outcome.state.section_candidates:
            assert len(cset.candidates) == settings.write_candidates_n
            assert cset.survivors
            assert (
                outcome.state.section_selections[cset.section_id] == cset.survivors[0].candidate_id
            )
        assert len(outcome.state.selected_drafts()) == 2
        assert len(fake_export) == 1


class TestLegacyResumeThroughAssemble:
    """게이트 제거 전 백엔드가 남긴 pending QA_SELECT의 재개 경로(payload 재수화)."""

    def _paused_payload_and_state(self, chunk: RetrievedChunk):
        """레거시 payload를 write_loop 산출물로 합성 — 후보 2절, 각 1개 생존."""
        plan = [
            SectionPlan(chapter_number=1, section_number=1, title="고령화 추이"),
            SectionPlan(chapter_number=2, section_number=1, title="비용편익 분석"),
        ]
        candidate_sets = [
            SectionCandidateSet(
                section_id=p.section_id,
                candidates=[
                    SectionCandidate(
                        draft=SectionDraft(
                            section_id=p.section_id,
                            content="이 섹션 본문입니다. [1] " * 30,
                            cited_chunk_ids=[chunk.chunk_id],
                        )
                    )
                ],
            )
            for p in plan
        ]
        state = ProjectState(
            user_id=uuid4(),
            topic="주제",
            section_plan=plan,
            section_candidates=candidate_sets,
            current_stage=ProjectStage.REVIEWING,
        )
        return qa_select_payload(state), state

    async def test_full_round_trip_completes(self, fake_write: RetrievedChunk):
        payload, state = self._paused_payload_and_state(fake_write)
        selections = {
            sec["section_id"]: sec["candidates"][0]["candidate_id"] for sec in payload["sections"]
        }

        # resume — 새 프로세스처럼 빈 state에서 payload로 재수화 + 선택 반영
        fresh = ProjectState(
            user_id=state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)

        done = await advance(fresh)
        assert isinstance(done, Done)
        assert done.state.current_stage is ProjectStage.COMPLETED
        assert len(done.state.selected_drafts()) == 2

    async def test_missing_selection_still_completes_but_incomplete_structure(
        self, fake_write: RetrievedChunk
    ):
        # 한 섹션만 선택 → assemble은 진행하되 selected_drafts는 1개 (structure 미완)
        payload, state = self._paused_payload_and_state(fake_write)
        first = payload["sections"][0]
        selections = {first["section_id"]: first["candidates"][0]["candidate_id"]}

        fresh = ProjectState(
            user_id=state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)
        done = await advance(fresh)
        assert isinstance(done, Done)
        assert len(done.state.selected_drafts()) == 1
