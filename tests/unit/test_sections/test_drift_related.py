"""미반영 연관 절·배치 순서 - 2026-08-28 흐름 추적("수정→저장→미반영→재작성")의 계약.

발견 구멍 셋의 회귀 고정:
  ① 연관 절 해석은 번호 라벨(parse_ref) - id 토큰 파서를 쓰면 항상 빈 결과
  ② 배치 실행은 클릭 순서가 아니라 의존 순서(상류 먼저)
  ③ (재적립·주입은 라우터/edit 배선 - 여기서는 순수 판정만 고정)
"""

from __future__ import annotations

from uuid import uuid4

from src.core.types import SectionPlan
from src.services.sections.drift import (
    SectionDrift,
    related_sections,
    rewrite_order,
)


def _plan(ch: int, sec: int, title: str, builds_on: list[str] | None = None) -> SectionPlan:
    return SectionPlan(
        section_id=uuid4(),
        chapter_number=ch,
        section_number=sec,
        title=title,
        chapter_title=f"{ch}장",
        direction="",
        key_points=[],
        analysts=[],
        search_queries=[],
        builds_on=builds_on or [],
    )


class TestRelatedSections:
    def test_number_and_chapter_refs(self) -> None:
        p12 = _plan(1, 2, "밸류체인")
        p31 = _plan(3, 1, "시장 규모", builds_on=["1.2"])
        p61 = _plan(6, 1, "SWOT", builds_on=["1.*"])
        p52 = _plan(5, 2, "해외 정책", builds_on=["3.1(지표)"])
        drifted = [
            SectionDrift(section_id=p12.section_id, label="1.2 밸류체인", reasons=("plan_changed",))
        ]
        rel = related_sections([p12, p31, p61, p52], drifted)
        labels = {r.label: r.via for r in rel}
        # 번호 참조와 장 전체 참조 모두 잡힌다. 3.1은 미반영이 아니므로 5.2는 안 뜬다.
        assert "3.1 시장 규모" in labels and labels["3.1 시장 규모"] == ("1.2 밸류체인",)
        assert "6.1 SWOT" in labels
        assert "5.2 해외 정책" not in labels
        # 미반영 절 자신은 연관 목록에 다시 안 뜬다.
        assert all("1.2" not in label for label in labels)

    def test_no_drift_no_related(self) -> None:
        assert related_sections([_plan(1, 1, "a")], []) == []


class TestRewriteOrder:
    def test_upstream_first_regardless_of_click_order(self) -> None:
        p12 = _plan(1, 2, "밸류체인")
        p31 = _plan(3, 1, "시장", builds_on=["1.2"])
        p61 = _plan(6, 1, "SWOT", builds_on=["3.*"])
        plans = [p12, p31, p61]
        # 클릭 순서: 하류부터 골랐어도 실행은 상류부터.
        ordered = rewrite_order(plans, {p61.section_id, p31.section_id, p12.section_id})
        assert ordered == [p12.section_id, p31.section_id, p61.section_id]

    def test_out_of_batch_dependency_is_free(self) -> None:
        p12 = _plan(1, 2, "밸류체인")
        p31 = _plan(3, 1, "시장", builds_on=["1.2"])
        # 1.2가 배치에 없으면 3.1은 제약 없이 그대로.
        assert rewrite_order([p12, p31], {p31.section_id}) == [p31.section_id]

    def test_unknown_target_appended(self) -> None:
        p12 = _plan(1, 2, "밸류체인")
        ghost = uuid4()  # plan에 없는 옛 절 - 막지 않고 끝에 붙인다
        out = rewrite_order([p12], {p12.section_id, ghost})
        assert out[0] == p12.section_id and ghost in out
