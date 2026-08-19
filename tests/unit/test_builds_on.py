"""builds_on 계약 — 표기 파서·정화·위상 정렬·배치 분할.

설계 확정본(2026-08-15)의 계약을 못 박는다: 표기 3형("4.1"·"4.1(총사업비)"·"4.*"),
깊이 하드캡 2, 절당 상한 2, 순환 절단, 유령 참조 제거. 전부 레벨 0이면 배치 1개
= 기존 동작 그대로(조사분석 실측 0/0/0 — 코드 분기 없음).
"""

from __future__ import annotations

from uuid import uuid4

from src.core.builds_on import (
    MAX_DEPTH,
    MAX_REFS_PER_SECTION,
    assign_levels,
    batches,
    clean_refs,
    parse_ref,
)
from src.core.types import SectionPlan


def _plan(label: str, builds_on: list[str] | None = None) -> SectionPlan:
    ch, sec = label.split(".")
    return SectionPlan(
        section_id=uuid4(),
        chapter_number=int(ch),
        section_number=int(sec),
        title=f"{label} 절",
        builds_on=builds_on or [],
    )


class TestParseRef:
    def test_section_ref(self):
        r = parse_ref("4.1")
        assert (r.chapter, r.section, r.metric) == (4, 1, None)

    def test_metric_ref(self):
        r = parse_ref("4.1(총사업비)")
        assert (r.chapter, r.section, r.metric) == (4, 1, "총사업비")
        assert r.label == "4.1(총사업비)"

    def test_chapter_wildcard(self):
        r = parse_ref("4.*")
        assert (r.chapter, r.section, r.metric) == (4, None, None)

    def test_wildcard_drops_metric(self):
        """장 전체 참조에 지표 지정은 뜻이 없다 - 지표만 버리고 장 참조로 읽는다."""
        r = parse_ref("4.*(총사업비)")
        assert (r.section, r.metric) == (None, None)

    def test_garbage_is_none_not_crash(self):
        for bad in ["", "4", "사.일", "4.1(", "4.1(a)(b)", None]:
            assert parse_ref(bad or "") is None


class TestCleanRefs:
    KNOWN = {"1.1", "1.2", "4.1", "4.2"}
    CHAPTERS = {1, 4}

    def _clean(self, raw, **kw):
        return clean_refs(
            raw,
            self_label="4.2",
            known_labels=self.KNOWN,
            known_chapters=self.CHAPTERS,
            **kw,
        )

    def test_keeps_valid_normalizes(self):
        out, warns = self._clean([" 4.1 ", "1.*"])
        assert out == ["4.1", "1.*"]
        assert warns == []

    def test_drops_ghost_and_self(self):
        out, warns = self._clean(["9.9", "4.2", "4.1"])
        assert out == ["4.1"]
        assert len(warns) == 2  # 유령 절 + 자기 참조

    def test_ghost_chapter_wildcard_dropped(self):
        out, warns = self._clean(["9.*"])
        assert out == []
        assert warns

    def test_cap_enforced(self):
        out, _ = self._clean(["1.1", "1.2", "4.1"])
        assert len(out) == MAX_REFS_PER_SECTION

    def test_planner_mode_demotes_metric(self):
        """플래너 LLM은 절 번호만 - 지표명은 유령 지표를 만들 수 있어 사람 전용."""
        out, _ = self._clean(["4.1(총사업비)"], allow_metric=False)
        assert out == ["4.1"]

    def test_human_mode_keeps_metric(self):
        out, _ = self._clean(["4.1(총사업비)"])
        assert out == ["4.1(총사업비)"]

    def test_dedup(self):
        out, _ = self._clean(["4.1", "4.1(총사업비)", "4.1"])
        # 같은 절이라도 지표 지정이 다르면 다른 계약이다
        assert out == ["4.1", "4.1(총사업비)"]


class TestAssignLevels:
    def test_no_deps_all_level_zero(self):
        plan = [_plan("1.1"), _plan("1.2"), _plan("2.1")]
        levels, warns = assign_levels(plan)
        assert set(levels.values()) == {0}
        assert warns == []

    def test_star_shape_two_levels(self):
        """실측 형태 - 의존 꼬리 전부 자기 의존 없음(별 모양)."""
        plan = [_plan("1.1"), _plan("1.2"), _plan("1.5", ["1.1", "1.2"])]
        levels, _ = assign_levels(plan)
        assert levels["1.1"] == 0 and levels["1.2"] == 0 and levels["1.5"] == 1

    def test_chapter_wildcard_depends_on_whole_chapter(self):
        plan = [_plan("4.1"), _plan("4.2"), _plan("4.5", ["4.*"])]
        levels, _ = assign_levels(plan)
        assert levels["4.5"] == 1

    def test_wildcard_excludes_self(self):
        """ "4.*"를 단 절 자신은 의존 대상에서 빠진다 - 아니면 항상 자기 순환이다."""
        plan = [_plan("4.1"), _plan("4.5", ["4.*"])]
        levels, warns = assign_levels(plan)
        assert levels["4.5"] == 1
        assert not any("순환" in w for w in warns)

    def test_depth_cap_demotes(self):
        plan = [
            _plan("1.1"),
            _plan("1.2", ["1.1"]),
            _plan("1.3", ["1.2"]),  # 레벨 2 → 캡(2)에 걸려 1로 강등
        ]
        levels, warns = assign_levels(plan)
        assert levels["1.3"] == MAX_DEPTH - 1
        assert any("깊이" in w for w in warns)

    def test_cycle_cut_with_warning(self):
        plan = [_plan("1.1", ["1.2"]), _plan("1.2", ["1.1"])]
        levels, warns = assign_levels(plan)
        assert any("순환" in w for w in warns)
        # 순환 참여 절은 절단돼 실행은 계속된다
        assert all(lv <= MAX_DEPTH - 1 for lv in levels.values())

    def test_metric_ref_still_creates_dependency(self):
        plan = [_plan("4.1"), _plan("6.2", ["4.1(총사업비)"])]
        levels, _ = assign_levels(plan)
        assert levels["6.2"] == 1


class TestBatches:
    def test_all_level_zero_is_single_batch(self):
        plan = [_plan("1.1"), _plan("1.2")]
        out, warns = batches(plan)
        assert len(out) == 1 and len(out[0]) == 2
        assert warns == []

    def test_two_batches_order(self):
        plan = [_plan("1.5", ["1.1"]), _plan("1.1"), _plan("2.1")]
        out, _ = batches(plan)
        assert [f"{s.chapter_number}.{s.section_number}" for s in out[0]] == ["1.1", "2.1"]
        assert [f"{s.chapter_number}.{s.section_number}" for s in out[1]] == ["1.5"]

    def test_empty_plan(self):
        assert batches([]) == ([], [])
