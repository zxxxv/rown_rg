"""수집 질의 타게팅 — 절 브리프 전달과 미커버 절 겨냥.

절 제목만으로 검색하면 '예산 산출' 같은 절이 빈손으로 남고, 보충 라운드는 이미
풍족한 절만 다시 긁어와 dedup으로 버려졌다(2026-08-09 예타 런 실측). 여기서
검증하는 계약은 둘: ① 방향·핵심포인트·관점이 질의 스펙에 실린다 ② focus_titles가
주어지면 그 절이 있는 챕터만, 그 절 제목만 남는다.
"""

from __future__ import annotations

from uuid import uuid4

from src.core.state import ProjectState
from src.core.types import SectionPlan
from src.workflows.stages import _chapter_groups, _section_brief


def _plan(ch: int, num: int, title: str, **kw) -> SectionPlan:
    return SectionPlan(section_id=uuid4(), chapter_number=ch, section_number=num, title=title, **kw)


def _state(plans: list[SectionPlan]) -> ProjectState:
    return ProjectState(project_id=uuid4(), user_id=uuid4(), topic="주제", section_plan=plans)


class TestSectionBrief:
    def test_carries_direction_key_points_and_analysts(self):
        brief = _section_brief(
            _plan(
                3,
                2,
                "예산 산출",
                direction="총사업비 산출 근거 제시",
                key_points=["단가 기준", "유사사업 사업비"],
                analysts=["시장분석", "정책동향"],
            )
        )
        assert "3.2 예산 산출" in brief
        assert "총사업비 산출 근거 제시" in brief
        assert "단가 기준" in brief and "유사사업 사업비" in brief
        assert "시장분석" in brief and "정책동향" in brief

    def test_bare_section_is_title_only(self):
        assert _section_brief(_plan(1, 1, "개요")) == "1.1 개요"


class TestChapterFocus:
    """_collect_sources의 focus 필터와 같은 규칙(챕터 그룹 → 겨냥 절만)."""

    def _focus(self, state: ProjectState, focus: set[str]):
        groups = [
            (n, t, [x for x in titles if x in focus]) for n, t, titles in _chapter_groups(state)
        ]
        return [g for g in groups if g[2]]

    def test_keeps_only_chapters_holding_uncovered_sections(self):
        state = _state(
            [_plan(1, 1, "배경"), _plan(1, 2, "필요성"), _plan(2, 1, "시장"), _plan(3, 1, "예산")]
        )
        focused = self._focus(state, {"필요성", "예산"})
        assert [(n, titles) for n, _, titles in focused] == [(1, ["필요성"]), (3, ["예산"])]

    def test_no_focus_means_every_chapter(self):
        state = _state([_plan(1, 1, "배경"), _plan(2, 1, "시장")])
        assert len(_chapter_groups(state)) == 2
