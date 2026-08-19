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
from src.core.exceptions import IncompleteReportError
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
from src.workflows.pipeline import Done, Outcome, Paused, advance
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

    async def _default_rules(_owner_id: object, _selected: object) -> list:
        # 규칙은 파일 카탈로그 기본값 — 개인 규칙 조회(DB) 없이 돈다.
        return []

    async def _no_catalog(_owner_id: object, _options: object = None) -> dict:
        # 개인 에이전트 카탈로그(DB) 없이 — 파일 카탈로그만으로 컨텍스트가 만들어진다.
        return {}

    async def _no_working_copy(_project_id: object) -> dict:
        # 인메모리 척추 검증 — DB 작업 사본 없음(편집 안 함과 동일).
        return {}

    # AI 실행 계획도 실LLM 없이 — 항상 유효한 최소 JSON을 주는 스텁(빈 계획은
    # _validate가 None으로 만들므로, 게이트 폴백까지 함께 검증하려면 별도 스텁 사용).
    monkeypatch.setattr(
        "src.workflows.stages._brief_client",
        _StubClient(
            '{"chapters":[{"chapter":1,"goal":"현황 정리"}],'
            '"sections":[{"chapter":1,"section":1,"goal":"개요 제시",'
            '"source_strategy":"정부 통계","writing_plan":"현황→시사점"}],'
            '"flows":[],"orphans":[],"query_splits":[]}'
        ),
    )

    async def _no_rehearse(state: ProjectState) -> ProjectState:
        # 검색 리허설(DB: index_version·section_rehearsals) 없이 — 전용 테스트가 따로 검증.
        return state

    async def _no_cache(retrieve: object, _state: ProjectState) -> object:
        # 리허설 캐시 래퍼(DB) 없이 — fake retriever를 그대로 쓴다.
        return retrieve

    async def _fixed_budget(_user_id: object) -> float:
        # 남은 한도 조회(DB) 없이 - 브리프 estimate의 remaining_limit_usd가 결정적이 된다.
        return 9_999.0

    monkeypatch.setattr("src.clients.llm.quota_gate.remaining_budget", _fixed_budget)
    monkeypatch.setattr("src.workflows.stages._rehearser", _no_rehearse)
    monkeypatch.setattr("src.workflows.stages._retrieval_cacher", _no_cache)
    monkeypatch.setattr("src.workflows.stages._exporter", _export)
    monkeypatch.setattr("src.workflows.stages._section_store", _no_store)
    monkeypatch.setattr("src.workflows.stages._draft_store", _no_draft_store)
    monkeypatch.setattr("src.workflows.stages._sections_cleaner", _no_cleaner)
    monkeypatch.setattr("src.workflows.stages._working_copy", _no_working_copy)
    monkeypatch.setattr("src.workflows.stages._analyst_catalog", _no_catalog)
    monkeypatch.setattr("src.workflows.stages._rule_texts", _default_rules)
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


# 장 제목이 있는 확정 목차 — 브리프 게이트 검증의 기본 입력(플래너 LLM 생략 경로).
_OUTLINE_CFG = {
    "outline": {
        "chapters": [
            {"title": "글로벌 RE100", "sections": [{"title": "개요"}]},
            {"title": "EU CBAM", "sections": [{"title": "개요"}]},
        ]
    }
}


async def _past_brief(state: ProjectState) -> Outcome:
    """설계 브리프 게이트를 통과시킨 뒤 다음 게이트까지 전진.

    브리프는 수집 **전** 게이트라 CREATED에서 출발하면 반드시 한 번 멈춘다.
    """
    brief = await advance(state)
    assert isinstance(brief, Paused)
    assert brief.review.gate is ReviewGate.DESIGN_BRIEF
    return await advance(brief.state.resolve_review(brief.review))


class TestPausesAtDesignBrief:
    """수집 전 설계 확인 — 무엇이 검색될지 사람이 먼저 본다(2026-08-14)."""

    async def test_first_gate_is_design_brief(self):
        outcome = await advance(ProjectState(user_id=uuid4(), topic="주제", options=_OUTLINE_CFG))
        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.DESIGN_BRIEF
        # 게이트보다 stage가 먼저 전이한다 — 재개 시 CREATED가 다시 매칭되면 무한 재개방.
        assert outcome.state.current_stage is ProjectStage.PLANNING

    async def test_brief_shows_the_query_that_will_run(self):
        outcome = await advance(ProjectState(user_id=uuid4(), topic="주제", options=_OUTLINE_CFG))
        assert isinstance(outcome, Paused)
        sections = outcome.review.payload["sections"]
        # 장 제목이 질의에 결합돼 장마다 다른 질의가 된다(탄소규제 런의 실패 지점).
        assert sections[0]["search_query"] == "글로벌 RE100 개요"
        assert sections[1]["search_query"] == "EU CBAM 개요"

    async def test_duplicate_queries_are_flagged(self):
        """장 제목이 없으면 같은 절 제목이 같은 질의가 된다 — 그 사실을 수집 전에 알린다."""
        outcome = await advance(
            ProjectState(
                user_id=uuid4(),
                topic="주제",
                options={
                    "outline": {
                        "chapters": [
                            {"title": "", "sections": [{"title": "개요"}]},
                            {"title": "", "sections": [{"title": "개요"}]},
                        ]
                    }
                },
            )
        )
        assert isinstance(outcome, Paused)
        assert outcome.review.payload["warnings"]["duplicate_query_sections"] == 2
        groups = outcome.review.payload["duplicate_queries"]
        assert [s["label"] for s in groups[0]["sections"]] == ["1.1 개요", "2.1 개요"]

    async def test_brief_carries_ai_plan_and_estimate(self):
        """AI 실행 계획(스텁)과 규모 추정이 게이트 payload에 실린다."""
        outcome = await advance(ProjectState(user_id=uuid4(), topic="주제", options=_OUTLINE_CFG))
        assert isinstance(outcome, Paused)
        plan = outcome.review.payload["ai_plan"]
        assert plan["sections"][0]["goal"] == "개요 제시"
        est = outcome.review.payload["estimate"]
        assert est["n_sections"] == 2
        assert est["cost_usd_max"] > est["cost_usd_min"] > 0
        # 분량 폴백(배정 없는 절 2개 × 게이트 기본 경계)
        assert est["total_min_chars"] == 400
        assert est["total_max_chars"] == 8000
        # 남은 한도가 예상 비용 옆에 실린다 - 부족해도 차단이 아니라 경고(사람 판단).
        assert est["remaining_limit_usd"] == 9_999.0
        # 경고 비교 기준은 모드별 런 1회 고정값(표준 $20) - 절 수에 흔들리지 않는다.
        assert est["expected_run_cost_usd"] == 20.0

    async def test_ai_plan_failure_does_not_block_gate(self, monkeypatch: pytest.MonkeyPatch):
        """LLM이 쓰레기를 돌려줘도 게이트는 결정적 브리프로 뜬다(ai_plan=None)."""
        monkeypatch.setattr("src.workflows.stages._brief_client", _StubClient("JSON이 아닌 응답"))
        outcome = await advance(ProjectState(user_id=uuid4(), topic="주제", options=_OUTLINE_CFG))
        assert isinstance(outcome, Paused)
        assert outcome.review.gate is ReviewGate.DESIGN_BRIEF
        assert outcome.review.payload["ai_plan"] is None
        assert outcome.review.payload["sections"]  # 결정적 내용은 그대로

    async def test_brief_shows_the_collection_query(self):
        """수집 질의도 실행과 같은 함수로 보여준다 — 주제문이 일하는 유일한 자리."""
        outcome = await advance(
            ProjectState(user_id=uuid4(), topic="글로벌 탄소규제 동향", options=_OUTLINE_CFG)
        )
        assert isinstance(outcome, Paused)
        chapters = outcome.review.payload["chapters"]
        assert [c["collection_query"] for c in chapters] == [
            "글로벌 탄소규제 동향 — 글로벌 RE100",
            "글로벌 탄소규제 동향 — EU CBAM",
        ]
        assert chapters[0]["section_titles"] == ["개요"]

    async def test_no_duplicates_when_chapters_differ(self):
        outcome = await advance(ProjectState(user_id=uuid4(), topic="주제", options=_OUTLINE_CFG))
        assert isinstance(outcome, Paused)
        assert outcome.review.payload["warnings"]["duplicate_query_sections"] == 0
        assert outcome.review.payload["duplicate_queries"] == []

    async def test_collection_has_not_started_yet(self, fake_research: _FakeResearchService):
        """브리프는 수집 전이다 — 여기서 멈추면 검색 콜이 한 번도 안 나가야 한다."""
        outcome = await advance(ProjectState(user_id=uuid4(), topic="주제", options=_OUTLINE_CFG))
        assert isinstance(outcome, Paused)
        assert fake_research.specs == []
        assert outcome.state.sources == []


class TestResearchPausesAtSourcePool:
    async def test_collect_plans_and_stages_before_gate(self, fake_research: _FakeResearchService):
        state = ProjectState(user_id=uuid4(), topic="인구 고령화 대응")  # CREATED
        outcome = await _past_brief(state)

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

        outcome = await _past_brief(ProjectState(user_id=uuid4(), topic="인구 고령화 대응"))
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
            await _past_brief(ProjectState(user_id=uuid4(), topic="인구 고령화 대응"))

    async def test_gate_payload_carries_source_signals(self, fake_research: _FakeResearchService):
        """자료 확정 게이트 payload에 사람이 취사선택할 신호가 실려 나온다."""
        outcome = await _past_brief(ProjectState(user_id=uuid4(), topic="인구 고령화 대응"))
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
        # 1) 설계 브리프 → research → SOURCE_POOL 정지
        paused_sources = await _past_brief(ProjectState(user_id=uuid4(), topic="주제"))
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

    async def test_missing_selection_fails_completion_gate(self, fake_write: RetrievedChunk):
        # 한 섹션만 선택 → 완성 게이트(2026-08-13)가 조립을 실패로 표면화한다 —
        # 빈 절을 실은 채 completed로 마감되지 않는다(6.1 실사고 재발 방지).
        payload, state = self._paused_payload_and_state(fake_write)
        first = payload["sections"][0]
        selections = {first["section_id"]: first["candidates"][0]["candidate_id"]}

        fresh = ProjectState(
            user_id=state.user_id, topic="주제", current_stage=ProjectStage.REVIEWING
        )
        fresh = rehydrate_from_payload(fresh, payload)
        fresh = apply_selection(fresh, selections)
        with pytest.raises(IncompleteReportError) as excinfo:
            await advance(fresh)
        assert "미작성 절 1개" in str(excinfo.value)
