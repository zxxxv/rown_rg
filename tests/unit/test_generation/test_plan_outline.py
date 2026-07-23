"""plan_from_outline 검증 — 사용자 확정 목차의 결정적(LLM 없는) SectionPlan 변환."""

from __future__ import annotations

import pytest

from src.services.generation.planner import MAX_SECTIONS, plan_from_outline


def _outline(*chapters: dict) -> dict:
    return {"chapters": list(chapters)}


class TestPlanFromOutline:
    def test_numbers_derive_from_position(self):
        plan = plan_from_outline(
            _outline(
                {"title": "1장", "sections": [{"title": "개요"}, {"title": "현황"}]},
                {"title": "2장", "sections": [{"title": "분석"}]},
            )
        )
        assert [(p.chapter_number, p.section_number, p.title) for p in plan] == [
            (1, 1, "개요"),
            (1, 2, "현황"),
            (2, 1, "분석"),
        ]

    def test_planner_outputs_carried(self):
        plan = plan_from_outline(
            _outline(
                {
                    "sections": [
                        {
                            "title": "규제 현황",
                            "direction": "조문 단위로",
                            "key_points": ["법령 계층", 3, "샌드박스"],
                            "analysts": ["정책분석", None],
                        }
                    ]
                }
            )
        )
        assert plan[0].direction == "조문 단위로"
        # 문자열 아닌 항목은 조용히 걸러진다 (프론트 계약 밖 입력 방어)
        assert plan[0].key_points == ["법령 계층", "샌드박스"]
        assert plan[0].analysts == ["정책분석"]

    def test_untitled_sections_dropped(self):
        plan = plan_from_outline(
            _outline({"sections": [{"title": "  "}, {"title": "유효"}, {"direction": "x"}]})
        )
        assert [p.title for p in plan] == ["유효"]

    def test_empty_outline_raises(self):
        with pytest.raises(ValueError, match="유효한 섹션"):
            plan_from_outline(_outline({"sections": []}))
        with pytest.raises(ValueError, match="유효한 섹션"):
            plan_from_outline({})

    def test_malformed_entries_tolerated(self):
        plan = plan_from_outline(
            {
                "chapters": [
                    None,
                    "문자열",
                    {"sections": "리스트아님"},
                    {"sections": [{"title": "생존"}]},
                ]
            }
        )
        # 챕터 번호는 위치 기반 — 앞의 쓰레기 항목도 자리를 차지한다(번호 안정성).
        assert [(p.chapter_number, p.title) for p in plan] == [(4, "생존")]

    def test_section_cap_enforced(self):
        big = _outline({"sections": [{"title": f"절{i}"} for i in range(MAX_SECTIONS + 5)]})
        plan = plan_from_outline(big)
        assert len(plan) == MAX_SECTIONS
