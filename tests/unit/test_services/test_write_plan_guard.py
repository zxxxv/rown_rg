"""작성 진입이 본문을 지우지 않는가 — 실사고 회귀 방지(2026-08-27).

35절짜리 완료 보고서를 다시 열고 자료 검토를 마쳤더니 **본문이 0절이 됐다.** 원인은
두 겹이었다:

1. config에 정본 plan(_section_plan)이 없는 옛 프로젝트라, 작성 진입 폴백이 목차 35절
   대신 2절짜리 최소 계획을 세웠다.
2. 그러면 저장된 35개 행이 전부 "계획 밖"으로 보여(done_ids 공집합) 잔재 청소기가
   통째로 지웠다.

확정 스냅샷이 있어 되살렸지만, 스냅샷이 없었으면 복구가 불가능했다.
"""

from __future__ import annotations

import uuid

from src.core.state import ProjectState
from src.workflows.stages import _ensure_section_plan


def _outline(n: int) -> dict:
    return {
        "chapters": [
            {
                "id": f"c{c}",
                "title": f"{c}장",
                "sections": [
                    {
                        "id": str(uuid.uuid4()),
                        "title": f"{c}.{s} 절",
                        "direction": "",
                        "key_points": [],
                        "agents": [],
                    }
                    for s in range(1, n + 1)
                ],
            }
            for c in (1, 2)
        ]
    }


class TestPlanFallback:
    def test_rebuilds_from_outline_instead_of_the_two_section_stub(self):
        """정본 plan이 없어도 **목차가 있으면 그것이 계획이다**.

        곧장 2절 폴백으로 떨어지면 저장된 절이 전부 계획 밖으로 보여 청소기가 지운다.
        """
        state = ProjectState(
            project_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            topic="주제",
            options={"outline": _outline(3)},
        )
        rebuilt = _ensure_section_plan(state)
        assert len(rebuilt.section_plan) == 6, "2장×3절이 그대로 계획이 돼야 한다"
        assert [s.title for s in rebuilt.section_plan][:2] == ["1.1 절", "1.2 절"]

    def test_outline_ids_are_kept_so_stored_rows_still_match(self):
        """목차의 절 안정 id를 그대로 쓴다 - 저장된 행과 id로 맞아야 안 지워진다."""
        outline = _outline(2)
        ids = [uuid.UUID(sec["id"]) for ch in outline["chapters"] for sec in ch["sections"]]
        state = ProjectState(
            project_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            topic="주제",
            options={"outline": outline},
        )
        got = [s.section_id for s in _ensure_section_plan(state).section_plan]
        assert got == ids

    def test_stub_only_when_there_is_no_outline_at_all(self):
        """목차조차 없을 때만 최소 계획 - 마지막 안전망은 남긴다."""
        state = ProjectState(
            project_id=uuid.uuid4(), user_id=uuid.uuid4(), topic="주제", options={}
        )
        plan = _ensure_section_plan(state).section_plan
        assert [s.title for s in plan] == ["개요", "분석"]

    def test_existing_plan_is_never_replaced(self):
        from src.core.types import SectionPlan

        keep = [SectionPlan(chapter_number=1, section_number=1, title="이미 있는 계획")]
        state = ProjectState(
            project_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            topic="주제",
            options={"outline": _outline(3)},
            section_plan=keep,
        )
        assert _ensure_section_plan(state).section_plan == keep
