"""PM 검증(pm_verify) — 챕터 그룹핑·JSON 파싱·숫자 다이제스트·경고 정규화 검증.

LLM은 stub, 역할 프롬프트(pm_verify_system)는 실카탈로그를 그대로 읽는다.
저장(persist_findings)은 DB 몫이라 통합 테스트로 미룬다.
"""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import (
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
)
from src.services.qa.pm_verify import (
    MAX_FINDINGS_PER_CHAPTER,
    _to_rows,
    numeric_digest,
    verify_report,
)


class _StubClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        text = self._responses[len(self.calls) % len(self._responses)]
        self.calls.append(request)
        return CompletionResponse(
            content=text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


def _state_two_chapters() -> ProjectState:
    """1장 1절 + 2장 1절, 각 1후보 선택 완료 상태."""
    plans = [
        SectionPlan(chapter_number=1, section_number=1, title="현황"),
        SectionPlan(chapter_number=2, section_number=1, title="분석"),
    ]
    contents = ["고령화율 24.1% 도달 [1]", "예산 1,500억 원 편성 [1]"]
    csets, selections = [], {}
    for plan, content in zip(plans, contents, strict=True):
        cand = SectionCandidate(
            draft=SectionDraft(section_id=plan.section_id, content=content, cited_chunk_ids=[])
        )
        csets.append(SectionCandidateSet(section_id=plan.section_id, candidates=[cand]))
        selections[plan.section_id] = cand.candidate_id
    state = ProjectState(user_id=uuid4(), topic="t", section_plan=plans, section_candidates=csets)
    for sid, cid in selections.items():
        state = state.record_selection(sid, cid)
    return state


class TestNumericDigest:
    def test_extracts_numbers_with_units_deduped(self):
        out = numeric_digest(["성장률 3.2% 및 3.2% 재언급, 1,500억 원 투입", "500명 대상"])
        assert out == ["3.2%", "1,500억 원", "500명"]

    def test_cap(self):
        texts = [" ".join(f"{i}건" for i in range(100))]
        assert len(numeric_digest(texts, cap=10)) == 10


class TestToRows:
    def test_normalizes_and_filters(self):
        manifest = {
            "findings": [
                {
                    "severity": "critical",
                    "category": "법령 시점",
                    "section": "2.1",
                    "detail": "상충",
                },
                {"severity": "이상한값", "category": None, "detail": "카테고리 없음"},
                {"detail": "   "},  # 빈 detail → 버림
                "문자열",  # dict 아님 → 버림
            ]
        }
        rows = _to_rows(2, manifest)
        assert len(rows) == 2
        assert rows[0]["severity"] == "critical"
        assert rows[0]["section_ref"] == "2.1"
        assert rows[1]["severity"] == "warning"  # 미지 severity는 warning으로
        assert rows[1]["category"] == "기타"

    def test_cap_per_chapter(self):
        manifest = {
            "findings": [
                {"severity": "warning", "category": "c", "detail": f"d{i}"}
                for i in range(MAX_FINDINGS_PER_CHAPTER + 10)
            ]
        }
        assert len(_to_rows(1, manifest)) == MAX_FINDINGS_PER_CHAPTER


class TestVerifyReport:
    async def test_one_call_per_chapter_and_rows_collected(self):
        stub = _StubClient(
            [
                '```json\n{"findings": []}\n```',
                '```json\n{"findings": [{"severity": "warning", "category": "통계 중복", '
                '"section": "2.1", "detail": "고령화율 중복 인용"}]}\n```',
            ]
        )
        rows = await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        assert len(stub.calls) == 2  # 챕터당 정확히 1콜 (비용 캡)
        assert [r["chapter_number"] for r in rows] == [2]
        assert rows[0]["category"] == "통계 중복"

    async def test_prev_chapter_digest_flows_to_next_call(self):
        stub = _StubClient(['```json\n{"findings": []}\n```'])
        await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        first, second = (c.messages[0].content for c in stub.calls)
        # 1장 호출에는 선행 다이제스트가 없고, 2장 호출에는 1장의 수치가 실린다.
        assert "이미 인용된 수치" not in first
        assert "24.1%" in second

    async def test_verification_system_prompt_loaded(self):
        stub = _StubClient(['```json\n{"findings": []}\n```'])
        await verify_report(_state_two_chapters(), client=stub, model="stub-model")
        system = stub.calls[0].system
        assert system is not None
        assert "검증" in system  # pm_verify_system 실카탈로그 로드 확인
        assert "JSON" in system  # 출력 계약 부착 확인
