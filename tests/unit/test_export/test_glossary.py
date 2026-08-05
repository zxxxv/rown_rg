"""약어 사전 빌더 — 결정적 추출 + LLM 설명 병합 검증 (stub LLM, DB 없음)."""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import SectionCandidate, SectionCandidateSet, SectionDraft, SectionPlan
from src.services.export.glossary import build_glossary, extract_abbreviations


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


def _state(bodies: list[str]) -> ProjectState:
    plans, csets, selections = [], [], {}
    for i, body in enumerate(bodies, start=1):
        plan = SectionPlan(chapter_number=1, section_number=i, title=f"절{i}")
        cand = SectionCandidate(
            draft=SectionDraft(section_id=plan.section_id, content=body, cited_chunk_ids=[])
        )
        plans.append(plan)
        csets.append(SectionCandidateSet(section_id=plan.section_id, candidates=[cand]))
        selections[plan.section_id] = cand.candidate_id
    state = ProjectState(
        user_id=uuid4(), topic="원전 정책", section_plan=plans, section_candidates=csets
    )
    for sid, cid in selections.items():
        state = state.record_selection(sid, cid)
    return state


class TestExtractAbbreviations:
    def test_first_seen_order_and_dedup(self):
        state = _state(
            [
                "ㅇ Small Modular Reactor(SMR)는 유망함 [1]\nㅇ SMR 재언급",
                "ㅇ 한국개발연구원(KDI)의 전망 [1]\nㅇ Small Modular Reactor(SMR) 또 병기",
            ]
        )
        assert extract_abbreviations(state) == {
            "SMR": "Small Modular Reactor",
            "KDI": "한국개발연구원",
        }

    def test_no_abbreviations(self):
        assert extract_abbreviations(_state(["약어 없는 본문임"])) == {}


class TestBuildGlossary:
    async def test_descriptions_merged_from_llm(self):
        stub = _StubClient('```json\n{"glossary": {"SMR": "소형 모듈 원자로"}}\n```')
        state = _state(["ㅇ Small Modular Reactor(SMR)와 한국개발연구원(KDI) [1]"])
        out = await build_glossary(state, client=stub, model="stub")
        assert out == {
            "SMR": {"full": "Small Modular Reactor", "desc": "소형 모듈 원자로"},
            "KDI": {"full": "한국개발연구원", "desc": ""},  # 모델이 빠뜨린 항목은 빈 설명
        }
        assert len(stub.calls) == 1  # 문서 전체에 1콜(비용 캡)

    async def test_no_abbrs_skips_llm(self):
        stub = _StubClient("{}")
        out = await build_glossary(_state(["약어 없음"]), client=stub, model="stub")
        assert out is None
        assert stub.calls == []
