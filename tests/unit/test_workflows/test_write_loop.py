"""write 루프 오케스트레이션 검증 — fake retriever + stub LLM으로 DB/네트워크 없이 완결."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)
from src.workflows import cancel
from src.workflows.cancel import RunCancelled
from src.workflows.write_loop import (
    apply_selection,
    check_assembled,
    overlay_working_copy,
    qa_select_payload,
    run_write_loop,
)


class _StubClient:
    """모든 호출에 같은 텍스트를 돌려주는 가짜 LLMClient."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


def _hard(check: str, passed: bool) -> GateResult:
    return GateResult(check=check, severity=CheckSeverity.HARD, passed=passed)


def _candset(section_id, n: int = 2) -> SectionCandidateSet:
    """모두 HARD 통과하는 후보 n개 묶음."""
    cands = [
        SectionCandidate(
            draft=SectionDraft(section_id=section_id, content=f"후보{i}", cited_chunk_ids=[]),
            report=StaticCheckReport(results=[_hard("renderable", True)]),
        )
        for i in range(n)
    ]
    return SectionCandidateSet(section_id=section_id, candidates=cands)


# ---------- run_write_loop (검색→생성→게이트 통합) ----------


class TestRunWriteLoop:
    async def test_produces_gated_candidates_per_section(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[s1, s2])
        chunk = RetrievedChunk(
            chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
        )

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return [chunk]

        # 길게 → bounds 통과, [1] → chunk 인용 resolves.
        stub = _StubClient("이 섹션의 본문입니다. [1] " * 30)
        result = await run_write_loop(state, retrieve=retrieve, client=stub, n=2)

        assert len(result.section_candidates) == 2
        for cset in result.section_candidates:
            assert len(cset.candidates) == 2
            assert len(cset.survivors) == 2  # 전부 HARD 통과
            assert cset.candidates[0].draft.cited_chunk_ids == [chunk.chunk_id]

    async def test_records_evidence_meta_per_section(self):
        """재료 지표는 본문이 아니라 state.section_meta로 — 화면 배지의 유일한 출처."""
        rich = SectionPlan(chapter_number=1, section_number=1, title="풍부", analysts=["정책동향"])
        poor = SectionPlan(chapter_number=1, section_number=2, title="빈약", analysts=["정책동향"])
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[rich, poor])

        def _chunk() -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
            )

        many = [_chunk() for _ in range(40)]

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return many if section.title == "풍부" else [_chunk()]

        result = await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("본문 [1] " * 30), n=1
        )
        assert result.section_meta[rich.section_id]["evidence_count"] == 40
        assert result.section_meta[rich.section_id]["volume_scaled"] is False
        assert result.section_meta[poor.section_id]["evidence_count"] == 1
        assert result.section_meta[poor.section_id]["volume_scaled"] is True

    async def test_empty_plan_yields_no_candidates(self):
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return []

        result = await run_write_loop(state, retrieve=retrieve, client=_StubClient("x"), n=2)
        assert result.section_candidates == []


# ---------- run_write_loop 병렬화 (순서·동일성·상한·취소) ----------


def _mk_state(titles: list[str]) -> tuple[ProjectState, RetrievedChunk]:
    plans = [
        SectionPlan(chapter_number=i + 1, section_number=1, title=t) for i, t in enumerate(titles)
    ]
    state = ProjectState(user_id=uuid4(), topic="주제", section_plan=plans)
    chunk = RetrievedChunk(
        chunk_id=uuid4(), source_id=uuid4(), content="근거 본문 " * 60, score=0.9
    )
    return state, chunk


class TestRunWriteLoopParallel:
    async def test_output_in_plan_order_despite_reversed_completion(self):
        """앞 절이 느려 완료 순서가 뒤집혀도 결과는 plan 순서 — gather 입력 순서 보존."""
        state, chunk = _mk_state(["느린절", "빠른절"])
        completed: list[str] = []

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            if section.title == "느린절":
                await asyncio.sleep(0.05)
            completed.append(section.title)
            return [chunk]

        result = await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
        )
        assert completed == ["빠른절", "느린절"]  # 실제로 병렬로 돌았다
        assert [cs.section_id for cs in result.section_candidates] == [
            p.section_id for p in state.section_plan
        ]
        assert list(result.section_meta) == [p.section_id for p in state.section_plan]

    async def test_parallel_result_equals_serial(self, monkeypatch):
        """같은 입력이면 동시성 1(직렬)과 4(병렬)의 산출이 내용까지 동일하다."""
        state, chunk = _mk_state(["개요", "분석", "전망"])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return [chunk]

        stub = _StubClient("이 섹션의 본문입니다. [1] " * 30)

        def _essence(result: ProjectState) -> list[tuple]:
            return [
                (
                    cs.section_id,
                    [c.draft.content for c in cs.candidates],
                    [c.draft.cited_chunk_ids for c in cs.candidates],
                )
                for cs in result.section_candidates
            ]

        monkeypatch.setattr(settings, "write_section_concurrency", 1)
        serial = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)
        monkeypatch.setattr(settings, "write_section_concurrency", 4)
        parallel = await run_write_loop(state, retrieve=retrieve, client=stub, n=1)

        assert _essence(serial) == _essence(parallel)
        assert serial.section_meta == parallel.section_meta

    async def test_semaphore_caps_concurrency(self, monkeypatch):
        """동시 실행 절 수가 write_section_concurrency를 넘지 않는다(그리고 병렬이긴 하다)."""
        monkeypatch.setattr(settings, "write_section_concurrency", 2)
        state, chunk = _mk_state([f"{i}절" for i in range(6)])
        active = 0
        peak = 0

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return [chunk]

        await run_write_loop(
            state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
        )
        assert peak == 2

    async def test_cancel_stops_queued_sections(self, monkeypatch):
        """작성 중 취소 — 큐에 있던 절은 시작 전에 멈추고 RunCancelled가 올라간다."""
        monkeypatch.setattr(settings, "write_section_concurrency", 1)
        state, chunk = _mk_state(["첫절", "둘째절", "셋째절"])
        retrieved: list[str] = []

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            retrieved.append(section.title)
            if section.title == "첫절":
                cancel.request(state.project_id)
            return [chunk]

        try:
            with pytest.raises(RunCancelled):
                await run_write_loop(
                    state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
                )
        finally:
            cancel.clear(state.project_id)
        assert retrieved == ["첫절"]  # 뒤 절들은 시작조차 안 했다

    async def test_cancel_aborts_inflight_sibling(self, monkeypatch):
        """취소 전파 시 진행 중이던 다른 절 태스크도 실제로 중단(cancel)된다."""
        monkeypatch.setattr(settings, "write_section_concurrency", 2)
        state, chunk = _mk_state(["막힌절", "신호절", "대기절"])
        blocked = asyncio.Event()
        aborted = asyncio.Event()

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            if section.title == "막힌절":
                blocked.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    aborted.set()
                    raise
            if section.title == "신호절":
                await blocked.wait()
                cancel.request(state.project_id)
            return [chunk]

        try:
            with pytest.raises(RunCancelled):
                await run_write_loop(
                    state, retrieve=retrieve, client=_StubClient("본문입니다. [1] " * 30), n=1
                )
        finally:
            cancel.clear(state.project_id)
        assert aborted.is_set()


# ---------- qa_select_payload (survivors 필터 + 경고 노출) ----------


class TestQaSelectPayload:
    def test_shows_only_survivors_with_warnings(self):
        section_id = uuid4()
        good = SectionCandidate(
            draft=SectionDraft(section_id=section_id, content="좋은 본문", cited_chunk_ids=[]),
            report=StaticCheckReport(
                results=[
                    _hard("citation_resolves", True),
                    GateResult(
                        check="bounds",
                        severity=CheckSeverity.SOFT,
                        passed=False,
                        detail="너무 짧음",
                    ),
                ]
            ),
        )
        bad = SectionCandidate(
            draft=SectionDraft(section_id=section_id, content="나쁜", cited_chunk_ids=[]),
            report=StaticCheckReport(results=[_hard("citation_resolves", False)]),
        )
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_candidates=[SectionCandidateSet(section_id=section_id, candidates=[good, bad])],
        )
        payload = qa_select_payload(state)
        sections = payload["sections"]
        assert isinstance(sections, list)
        cands = sections[0]["candidates"]
        assert len(cands) == 1  # HARD 실패한 bad 제외
        assert cands[0]["candidate_id"] == str(good.candidate_id)
        assert cands[0]["warnings"][0]["check"] == "bounds"
        assert sections[0]["all_excluded"] is False

    def test_all_excluded_flag(self):
        section_id = uuid4()
        bad = SectionCandidate(
            draft=SectionDraft(section_id=section_id, content="x", cited_chunk_ids=[]),
            report=StaticCheckReport(results=[_hard("renderable", False)]),
        )
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_candidates=[SectionCandidateSet(section_id=section_id, candidates=[bad])],
        )
        payload = qa_select_payload(state)
        sections = payload["sections"]
        assert sections[0]["all_excluded"] is True
        assert sections[0]["candidates"] == []


# ---------- apply_selection + check_assembled ----------


class TestSelectionAndAssembly:
    def test_selection_records_and_assembles_in_plan_order(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        cs1, cs2 = _candset(s1.section_id), _candset(s2.section_id)
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_plan=[s1, s2],
            section_candidates=[cs1, cs2],
        )
        selections = {
            str(s1.section_id): str(cs1.candidates[1].candidate_id),
            str(s2.section_id): str(cs2.candidates[0].candidate_id),
        }
        state = apply_selection(state, selections)
        drafts, result = check_assembled(state)
        assert result.passed is True
        # 순서 = plan, 내용 = 선택된 후보
        assert [d.content for d in drafts] == ["후보1", "후보0"]

    def test_missing_selection_fails_structure(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        cs1, cs2 = _candset(s1.section_id), _candset(s2.section_id)
        state = ProjectState(
            user_id=uuid4(),
            topic="t",
            section_plan=[s1, s2],
            section_candidates=[cs1, cs2],
        )
        # s2 선택 누락
        state = apply_selection(state, {str(s1.section_id): str(cs1.candidates[0].candidate_id)})
        drafts, result = check_assembled(state)
        assert len(drafts) == 1
        assert result.passed is False
        assert "누락 섹션 1개" in result.detail


# ---------- overlay_working_copy (검토 중 편집 → 조립 반영) ----------


class TestOverlayWorkingCopy:
    def test_row_content_overrides_candidates(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        cs1 = _candset(s1.section_id)
        state = ProjectState(
            user_id=uuid4(), topic="t", section_plan=[s1], section_candidates=[cs1]
        )
        cited = [uuid4()]
        state = overlay_working_copy(state, {s1.section_id: ("사람이 고친 본문", cited)})
        for cand in state.section_candidates[0].candidates:
            assert cand.draft.content == "사람이 고친 본문"
            assert cand.draft.cited_chunk_ids == cited

    def test_empty_or_missing_row_keeps_candidates(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        s2 = SectionPlan(chapter_number=2, section_number=1, title="분석")
        cs1, cs2 = _candset(s1.section_id), _candset(s2.section_id)
        state = ProjectState(
            user_id=uuid4(), topic="t", section_plan=[s1, s2], section_candidates=[cs1, cs2]
        )
        # s1은 빈 내용(failed 행), s2는 행 없음 — 둘 다 payload 후보 유지
        state = overlay_working_copy(state, {s1.section_id: ("   ", [])})
        assert state.section_candidates[0].candidates[0].draft.content == "후보0"
        assert state.section_candidates[1].candidates[0].draft.content == "후보0"

    def test_filled_empty_section_promoted_and_selected(self):
        s1 = SectionPlan(chapter_number=1, section_number=1, title="개요")
        empty = SectionCandidateSet(section_id=s1.section_id, candidates=[])
        state = ProjectState(
            user_id=uuid4(), topic="t", section_plan=[s1], section_candidates=[empty]
        )
        state = overlay_working_copy(state, {s1.section_id: ("직접 채운 본문", [])})
        drafts, result = check_assembled(state)
        assert result.passed is True
        assert [d.content for d in drafts] == ["직접 채운 본문"]
