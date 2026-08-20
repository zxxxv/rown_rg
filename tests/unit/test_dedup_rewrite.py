"""3층 국소 재작성 - 대상 선정·결정적 검증·상태 재조립."""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import (
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)
from src.services.qa.dedup_rewrite import (
    _validate,
    dedup_rewrite_state,
    find_targets,
)

DUP = (
    "국내 재생에너지 조달 여건은 제한적 PPA 선택지와 높은 비용, "
    "계통 제약으로 어렵다고 평가됨(출처 3)"
)


def _sections():
    first = f"ㅇ 도입.\n\n{DUP}\n\nㅇ 1장 고유 내용."
    trail = f"ㅇ 4장 도입.\n\n{DUP} ㅇ 그리고 4장 고유 진단이 이어짐(출처 5)\n\nㅇ 결론."
    return [("1.2", first), ("4.2", trail)]


class TestFindTargets:
    def test_trailing_paragraph_targeted(self):
        targets = find_targets(_sections())
        assert len(targets) == 1
        t = targets[0]
        assert t.section_ref == "4.2"
        assert t.counterpart_ref == "1.2"
        assert t.para_index == 1

    def test_no_duplicates_no_targets(self):
        assert find_targets([("1.1", "ㅇ 전혀 다른 내용."), ("2.1", "ㅇ 또 다른 서술.")]) == []


class TestValidate:
    ORIG = f"{DUP} 추가로 34.8% 수치가 있음(출처 7)"

    def test_new_number_rejected(self):
        out = _validate(self.ORIG, "압축 결과에 99.9% 새 수치(출처 7)")
        assert out and "새 수치" in out

    def test_new_marker_rejected(self):
        out = _validate(self.ORIG, "압축 결과(출처 99)")
        assert out and "마커" in out

    def test_longer_rejected(self):
        assert _validate("짧은 원문", "훨씬 더 길어진 재작성 결과물입니다") is not None

    def test_reference_style_passes(self):
        out = _validate(self.ORIG, "(1.2절 참조) 조달 여건 제약 위에 34.8%가 유지됨(출처 7)")
        assert out is None


class _StubClient:
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


def _state():
    plans = [
        SectionPlan(chapter_number=1, section_number=2, title="여건"),
        SectionPlan(chapter_number=4, section_number=2, title="대응"),
    ]
    secs = _sections()
    sets, selections = [], {}
    for plan, (_, content) in zip(plans, secs, strict=True):
        cand = SectionCandidate(
            draft=SectionDraft(section_id=plan.section_id, content=content, cited_chunk_ids=[]),
            report=StaticCheckReport(results=[]),
        )
        sets.append(SectionCandidateSet(section_id=plan.section_id, candidates=[cand]))
        selections[plan.section_id] = cand.candidate_id
    return ProjectState(
        user_id=uuid4(),
        topic="주제",
        section_plan=plans,
        section_candidates=sets,
        section_selections=selections,
    )


class TestDedupRewriteState:
    async def test_rewrites_trailing_paragraph_only(self):
        stub = _StubClient("(1.2절 참조) 조달 제약 위에 4장 고유 진단이 이어짐(출처 5)")
        state, n = await dedup_rewrite_state(_state(), model="stub", client=stub)
        assert n == 1
        drafts = {d.section_id: d.content for d in state.selected_drafts()}
        plans = {(s.chapter_number, s.section_number): s.section_id for s in state.section_plan}
        assert "(1.2절 참조)" in drafts[plans[(4, 2)]]
        assert DUP in drafts[plans[(1, 2)]]  # 정본 절은 무변
        assert drafts[plans[(4, 2)]].startswith("ㅇ 4장 도입.")  # 문단 밖 무변

    async def test_rejected_rewrite_keeps_original(self):
        stub = _StubClient("완전히 새로운 주장에 77.7%와 (출처 42)를 얹은 긴 결과")
        state, n = await dedup_rewrite_state(_state(), model="stub", client=stub)
        assert n == 0
        drafts = {d.section_id: d.content for d in state.selected_drafts()}
        plans = {(s.chapter_number, s.section_number): s.section_id for s in state.section_plan}
        assert DUP in drafts[plans[(4, 2)]]
