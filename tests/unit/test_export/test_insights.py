"""시사점 요약 빌더·별도 한글 파일 렌더 검증 (stub LLM, DB 없음).

핵심 계약: **제목이 시사점·제언인 절만 골라 넣는다.** 프리셋 10종에서 그 절들이 이미
그 장(또는 보고서 전체)의 결론을 담고, 나머지 본문까지 밀어 넣으면 입력이 20만 자로
불어난다. 1순위가 비면(자유주제) 마지막 장으로 폴백한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.state import ProjectState
from src.core.types import SectionCandidate, SectionCandidateSet, SectionDraft, SectionPlan
from src.export.hwpx_writer import Heading, Paragraph
from src.services.export.insights import (
    INSIGHTS_HEADING,
    MAX_INPUT_CHARS,
    build_insights,
    build_system,
    collect_insight_sections,
    export_insights,
    insights_blocks,
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

    async def test_drops_whole_sections_not_mid_sentence(self):
        """상한을 넘으면 **절 단위로** 뺀다 - 반토막 절은 잘못 요약된다.

        종전에는 이어 붙인 글을 통째로 잘라 마지막 절이 문장 중간에서 끊긴 채
        들어갔다. 모델은 그것도 온전한 절로 읽는다.
        """
        client = _StubClient('```json\n{"insights": "## 핵심 요약"}\n```')
        half = MAX_INPUT_CHARS // 2 + 1_000
        state = _state(
            [
                (6, 1, "제언 하나", "가" * half),
                (6, 2, "제언 둘", "나" * half),
                (6, 3, "제언 셋", "다" * 500),
            ]
        )

        out = await build_insights(state, client=client, model="stub-model")

        assert out is not None
        sent = client.calls[0].messages[0].content
        assert "6.1 제언 하나" in sent
        assert "6.2 제언 둘" not in sent, "넘치는 절은 통째로 빠져야 한다"
        assert "6.3 제언 셋" not in sent, "앞에서 끊었으면 뒤도 담지 않는다(순서가 뜻이다)"
        # 반토막 흔적이 없어야 한다 - 담긴 절은 통째로 담겼다(지시문에도 '가'가
        # 있어 세는 대신 그 절의 본문 전체가 통으로 들어갔는지를 본다).
        assert "가" * half in sent

    async def test_records_what_the_model_never_saw(self):
        """빠진 절을 기록하고, 근거 목록에서도 뺀다.

        요약의 유일한 검증 수단은 '무엇을 보고 썼나'다. 모델이 못 본 절이 근거로
        적혀 있으면 그 기록이 거짓말이 된다.
        """
        client = _StubClient('```json\n{"insights": "## 핵심 요약"}\n```')
        half = MAX_INPUT_CHARS // 2 + 1_000
        state = _state([(6, 1, "제언 하나", "가" * half), (6, 2, "제언 둘", "나" * half)])

        out = await build_insights(state, client=client, model="stub-model")

        assert out is not None
        assert out["source_sections"] == ["6.1 제언 하나"]
        assert out["dropped_sections"] == ["6.2 제언 둘"]
        assert len(out["source_section_ids"]) == 1

    async def test_nothing_dropped_when_it_fits(self):
        client = _StubClient('```json\n{"insights": "## 핵심 요약"}\n```')
        state = _state([(6, 1, "제언 하나", "가" * 500), (6, 2, "제언 둘", "나" * 500)])

        out = await build_insights(state, client=client, model="stub-model")

        assert out is not None
        assert out["dropped_sections"] == []
        assert out["source_sections"] == ["6.1 제언 하나", "6.2 제언 둘"]


class TestInsightsBlocks:
    """요약 마크다운 → 블록. 표지·목차 없이 제목 한 줄과 본문만 나가는 브리핑 문서다."""

    def _brief_state(self) -> ProjectState:
        return ProjectState(
            user_id=uuid4(),
            topic="탄소규제 대응",
            title="EU 탄소국경조정제도 대응 전략",
            created_at=datetime(2026, 8, 20, 3, 0, tzinfo=UTC),  # KST 12:00
        )

    def test_leads_with_heading_and_origin_line(self):
        blocks = insights_blocks(
            self._brief_state(),
            """## 핵심 요약

□ 첫 항목""",
        )

        assert blocks[0] == Heading(level=1, text=INSIGHTS_HEADING)
        # 파일이 따로 돌아다녀도 어느 보고서의 요약인지 알아야 한다(작성일은 KST 표시).
        assert isinstance(blocks[1], Paragraph)
        assert "EU 탄소국경조정제도 대응 전략" in blocks[1].text
        assert "2026년 08월 20일" in blocks[1].text

    def test_body_keeps_outline_levels(self):
        blocks = insights_blocks(
            self._brief_state(),
            """## 제언

□ 대주제
ㅇ 중간
- 세부""",
        )

        body = blocks[2:]
        assert body[0] == Heading(level=3, text="제언")
        assert [b.indent for b in body[1:]] == [0, 1, 2]

    def test_strips_leaked_source_marks(self):
        """이 파일엔 참고문헌이 없다 — 새어 나온 (출처 n)은 가리킬 데가 없는 번호가 된다."""
        blocks = insights_blocks(self._brief_state(), "□ 배출량 12% 감소 (출처 7)")

        assert blocks[-1].text == "□ 배출량 12% 감소"

    def test_falls_back_to_topic_when_untitled(self):
        """제목 없이 만든 옛 프로젝트도 첫 줄이 비지 않게 — 표지 규칙과 같은 폴백."""
        state = ProjectState(user_id=uuid4(), topic="탄소규제 대응")

        assert "탄소규제 대응" in insights_blocks(state, "□ 항목")[1].text


class TestExportInsights:
    def test_writes_separate_hwpx_file(self, tmp_path: Path):
        """본문 완성본과 다른 폴더·같은 이름 규칙 — 섞이지 않고 렌더 버전은 함께 붙는다."""
        from src.services.export.report import export_filename

        state = ProjectState(user_id=uuid4(), topic="탄소규제 대응", title="대응 전략")

        path = export_insights(
            state,
            """## 핵심 요약

□ 첫 항목""",
            output_dir=tmp_path,
        )

        assert path == tmp_path / export_filename(state.project_id)
        assert path.exists()
        assert path.stat().st_size > 0


class TestExplicitSelection:
    """사람이 고른 절만 근거로 삼는다 - 자동 선택은 제목 규칙에 기대는 추측이다."""

    def test_uses_only_the_chosen_sections(self):
        state = _state(
            [
                (1, 1, "개요", "가"),
                (1, 5, "시사점", "나"),
                (2, 3, "국내 기업 대응수준 진단", "다"),
            ]
        )
        # 제목 규칙대로면 1.5만 뽑히지만, 사람이 1.1과 2.3을 골랐다.
        chosen = [state.section_plan[0].section_id, state.section_plan[2].section_id]
        picked = collect_insight_sections(state, chosen)
        assert [label for label, _ in picked] == ["1.1 개요", "2.3 국내 기업 대응수준 진단"]

    def test_keeps_outline_order_regardless_of_argument_order(self):
        state = _state([(1, 1, "가", "A"), (2, 1, "나", "B"), (3, 1, "다", "C")])
        ids = [p.section_id for p in state.section_plan]
        picked = collect_insight_sections(state, [ids[2], ids[0]])
        assert [label for label, _ in picked] == ["1.1 가", "3.1 다"]

    def test_falls_back_to_auto_when_chosen_ids_are_all_gone(self):
        # 고른 절이 목차에서 사라진 뒤 - 빈 요약을 내는 대신 자동 선택으로 돌아간다.
        state = _state([(1, 1, "개요", "가"), (1, 5, "시사점", "나")])
        picked = collect_insight_sections(state, [uuid4()])
        assert [label for label, _ in picked] == ["1.5 시사점"]

    def test_empty_selection_is_same_as_no_selection(self):
        state = _state([(1, 1, "개요", "가"), (1, 5, "시사점", "나")])
        assert collect_insight_sections(state, []) == collect_insight_sections(state, None)


class TestCommonTemplate:
    """프리셋 무관 공통 틀 - 세 덩어리와 규칙 두 개가 프롬프트에 박혀 있어야 한다."""

    def test_implication_count_is_substituted(self):
        assert "시사점 정확히 3개" in build_system(3)
        assert "시사점 정확히 4개" in build_system(4)

    def test_carries_three_slots_and_two_rules(self):
        sys_ = build_system()
        for slot in ("## 한눈에", "## 시사점", "## 종합 판단"):
            assert slot in sys_
        for stage in ("**확인된 것**", "**뜻하는 것**", "**달라져야 하는 것**"):
            assert stage in sys_
        # 규칙은 금지형이 아니라 "있는 값은 옮기고 없는 값은 만들지 마라"여야 한다 -
        # 금지만 말했더니 모델이 수치를 통째로 회피해 B/C 값이 빠졌다(골든 v1).
        assert "그대로 옮겨 쓴다" in sys_
        assert "없는 값은 만들지 마라" in sys_
        assert "주체를 붙일 수 없는 문장은 제언이 아니다" in sys_
