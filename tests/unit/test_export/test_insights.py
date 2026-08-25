"""시사점 요약 빌더 — 입력 절 선별과 매니페스트 파싱 검증 (stub LLM, DB 없음).

핵심 계약: **제목이 시사점·제언인 절만 골라 넣는다.** 프리셋 10종에서 그 절들이 이미
그 장(또는 보고서 전체)의 결론을 담고, 나머지 본문까지 밀어 넣으면 입력이 20만 자로
불어난다. 1순위가 비면(자유주제) 마지막 장으로 폴백한다.
"""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import SectionCandidate, SectionCandidateSet, SectionDraft, SectionPlan
from src.services.export.insights import (
    MAX_INPUT_CHARS,
    build_insights,
    collect_insight_sections,
)


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


def _state(sections: list[tuple[int, int, str, str]]) -> ProjectState:
    """(장, 절, 제목, 본문) 목록 → 전 절이 선택 확정된 상태."""
    plans, csets, selections = [], [], {}
    for chapter, number, title, body in sections:
        plan = SectionPlan(chapter_number=chapter, section_number=number, title=title)
        cand = SectionCandidate(
            draft=SectionDraft(section_id=plan.section_id, content=body, cited_chunk_ids=[])
        )
        plans.append(plan)
        csets.append(SectionCandidateSet(section_id=plan.section_id, candidates=[cand]))
        selections[plan.section_id] = cand.candidate_id
    state = ProjectState(
        user_id=uuid4(), topic="탄소규제 대응", section_plan=plans, section_candidates=csets
    )
    for sid, cid in selections.items():
        state = state.record_selection(sid, cid)
    return state


class TestCollectInsightSections:
    def test_picks_only_insight_titled_sections(self):
        state = _state(
            [
                (1, 1, "연구 배경", "ㅇ 배경 본문"),
                (2, 1, "국내 동향", "ㅇ 동향 본문"),
                (2, 5, "환경분석 종합 및 시사점", "ㅇ 소결 본문"),
                (6, 2, "핵심 시사점 및 제언", "ㅇ 결론 본문"),
            ]
        )
        assert collect_insight_sections(state) == [
            ("2.5 환경분석 종합 및 시사점", "ㅇ 소결 본문"),
            ("6.2 핵심 시사점 및 제언", "ㅇ 결론 본문"),
        ]

    def test_matches_next_step_and_conclusion_titles(self):
        """프리셋 실제 표기 — '핵심 제언 및 Next Step'·'결론 및 정책 제언'."""
        state = _state(
            [
                (1, 1, "현황", "ㅇ 현황"),
                (6, 2, "핵심 제언 및 Next Step", "ㅇ 제언"),
            ]
        )
        assert [label for label, _ in collect_insight_sections(state)] == [
            "6.2 핵심 제언 및 Next Step"
        ]

    def test_falls_back_to_last_chapter_when_no_match(self):
        """자유주제라 제목에 시사점이 없어도 빈손으로 돌아가지 않는다."""
        state = _state(
            [
                (1, 1, "배경", "ㅇ 배경"),
                (3, 1, "정리", "ㅇ 정리 하나"),
                (3, 2, "향후 과제", "ㅇ 정리 둘"),
            ]
        )
        assert [label for label, _ in collect_insight_sections(state)] == [
            "3.1 정리",
            "3.2 향후 과제",
        ]

    def test_empty_when_no_selected_drafts(self):
        state = ProjectState(user_id=uuid4(), topic="빈 보고서")
        assert collect_insight_sections(state) == []


class TestBuildInsights:
    async def test_returns_content_and_source_labels(self):
        client = _StubClient('```json\n{"insights": "## 핵심 요약\\n\\n\\u25a1 첫 항목"}\n```')
        state = _state([(6, 2, "시사점 및 제언", "ㅇ 결론 본문 (출처 3)")])

        result = await build_insights(state, client=client, model="stub-model")

        assert result is not None
        assert result["content"] == "## 핵심 요약\n\n□ 첫 항목"
        assert result["source_sections"] == ["6.2 시사점 및 제언"]
        assert result["model"] == "stub-model"

    async def test_strips_citation_marks_from_input(self):
        """(출처 n)은 작성 단계의 근거 표식이지 요약 원문이 아니다."""
        client = _StubClient('```json\n{"insights": "## 핵심 요약"}\n```')
        state = _state([(6, 2, "시사점", "ㅇ 배출량 12% 감소 (출처 7)")])

        await build_insights(state, client=client, model="stub-model")

        sent = client.calls[0].messages[0].content
        assert "(출처 7)" not in sent
        assert "배출량 12% 감소" in sent

    async def test_returns_none_when_nothing_to_summarize(self):
        client = _StubClient('```json\n{"insights": "무시됨"}\n```')
        state = ProjectState(user_id=uuid4(), topic="빈 보고서")

        assert await build_insights(state, client=client) is None
        assert client.calls == []

    async def test_returns_none_on_empty_manifest(self):
        """모델이 빈 값을 주면 저장하지 않는다 — 빈 화면이 잘못된 요약보다 낫다."""
        client = _StubClient('```json\n{"insights": "   "}\n```')
        state = _state([(6, 2, "시사점", "ㅇ 본문")])

        assert await build_insights(state, client=client) is None

    async def test_caps_input_length(self):
        client = _StubClient('```json\n{"insights": "## 핵심 요약"}\n```')
        state = _state([(6, 2, "시사점", "가" * (MAX_INPUT_CHARS + 10_000))])

        await build_insights(state, client=client, model="stub-model")

        sent = client.calls[0].messages[0].content
        # 주제·지시문이 앞에 붙으니 본문 몫만 상한을 넘지 않으면 된다.
        assert len(sent) < MAX_INPUT_CHARS + 1_000
