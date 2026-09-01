"""런 퇴장의 plan 재도장 가드 - "저장 → 재개 → 옛 목차 회귀" 루프의 회귀 고정.

2026-08-28 실사고: 사용자가 목차를 8절→11절로 저장(병합 정상)했는데, 실패한 런이
퇴장하며 자기 메모리의 옛 8절 plan을 config에 재도장 - 저장할 때마다 재개가 되돌려
세 번 반복됐다. 계약: 런 시작 시점의 plan 지문과 퇴장 시점 config가 다르면(도중
저장) 도장을 건너뛰고 config의 병합 결과를 보존한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from src.core.section_plan import SECTION_PLAN_KEY
from src.db.models.project import Project
from src.workflows import runner


def _plan_items(n: int, tag: str) -> list[dict]:
    from uuid import uuid4

    return [
        {
            "section_id": str(uuid4()),
            "chapter_number": 1,
            "section_number": i + 1,
            "title": f"{tag}-{i + 1}",
            "chapter_title": "1장",
            "direction": "",
            "key_points": [],
            "analysts": [],
            "search_queries": [],
            "builds_on": [],
        }
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _wire(monkeypatch, test_session_maker):
    monkeypatch.setattr("src.workflows.runner.async_session_maker", test_session_maker)
    monkeypatch.setattr("src.db.session.async_session_maker", test_session_maker)


class TestPlanStampGuard:
    async def test_mid_run_outline_save_survives_run_exit(
        self, super_admin_user, test_session, test_session_maker, monkeypatch
    ) -> None:
        old_plan = _plan_items(2, "old")
        new_plan = _plan_items(3, "new")
        project = Project(
            title="도장 가드",
            topic="철강",
            preset=None,
            config={SECTION_PLAN_KEY: old_plan},
            depth_mode="standard",
            owner_id=super_admin_user.id,
            status="reviewing",
        )
        test_session.add(project)
        await test_session.commit()

        async def _advance(state, on_stage=None):
            # 런 도중 사용자가 목차를 저장한 상황 - 딴 세션이 병합된 새 plan을 커밋.
            async with test_session_maker() as s2:
                row = await s2.get(Project, project.id)
                row.config = {**(row.config or {}), SECTION_PLAN_KEY: new_plan}
                await s2.commit()
            return SimpleNamespace(state=state, review=None)

        monkeypatch.setattr(runner, "advance", _advance)
        await runner._execute(project.id)

        async with test_session_maker() as fresh:
            cfg = (
                await fresh.execute(
                    text("SELECT config FROM projects WHERE id = :p"), {"p": str(project.id)}
                )
            ).scalar_one()
        stored = (cfg or {}).get(SECTION_PLAN_KEY) or []
        # 퇴장 도장이 옛 2절 plan으로 되돌리면 안 된다 - 도중 저장된 3절이 보존돼야.
        assert len(stored) == 3
        assert stored[0]["title"].startswith("new")

    async def test_unchanged_plan_still_stamped(
        self, super_admin_user, test_session, test_session_maker, monkeypatch
    ) -> None:
        # 도중 저장이 없으면 종전 계약 그대로 - 런의 plan이 config에 새겨진다.
        plan = _plan_items(2, "run")
        project = Project(
            title="도장 유지",
            topic="철강",
            preset=None,
            config={},
            depth_mode="standard",
            owner_id=super_admin_user.id,
            status="reviewing",
        )
        test_session.add(project)
        await test_session.commit()

        async def _advance(state, on_stage=None):
            from src.core.section_plan import load_section_plan

            return SimpleNamespace(
                state=state.with_section_plan(load_section_plan(plan)), review=None
            )

        monkeypatch.setattr(runner, "advance", _advance)
        await runner._execute(project.id)

        async with test_session_maker() as fresh:
            cfg = (
                await fresh.execute(
                    text("SELECT config FROM projects WHERE id = :p"), {"p": str(project.id)}
                )
            ).scalar_one()
        stored = (cfg or {}).get(SECTION_PLAN_KEY) or []
        assert len(stored) == 2 and stored[0]["title"].startswith("run")
