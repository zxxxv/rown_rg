"""write 루프 오케스트레이션 검증 — fake retriever + stub LLM으로 DB/네트워크 없이 완결."""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
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

    async def test_empty_plan_yields_no_candidates(self):
        state = ProjectState(user_id=uuid4(), topic="주제", section_plan=[])

        async def retrieve(section: SectionPlan) -> list[RetrievedChunk]:
            return []

        result = await run_write_loop(state, retrieve=retrieve, client=_StubClient("x"), n=2)
        assert result.section_candidates == []


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
