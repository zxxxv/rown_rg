"""resume_from 소비는 페이즈 진입 전에 영속된다 — 자기교착 사고의 회귀 고정.

2026-08-27 운영 3연속 실측: _execute가 resume_from을 pop하며 config를 더럽힌 채
커밋 없이 진행하면, 다음 쿼리의 autoflush가 미커밋 UPDATE로 projects 행 잠금을
쥐고 페이즈에 들어간다. 진행 표시용 별도 세션(_persist_running_stage)의 status
UPDATE가 그 잠금을 기다리며 러너가 무한 정지했고, 폴링이 뒤에 쌓여 커넥션 풀
고갈(5+10)로 API가 전면 500이 됐다. 잠긴 채 죽으니 소비도 영속되지 않아 다음
재개가 또 자료 단계로 되감겼다.

계약: pop 직후 즉시 커밋 — 이후 단계가 무엇으로 죽든 resume_from은 이미 소비돼
있어야 한다(잠금 해제와 소비 영속을 한 커밋이 보장한다).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.workflows import runner


@pytest.fixture(autouse=True)
def _wire_session_maker(monkeypatch, test_session_maker):
    # runner는 모듈 수준에서 async_session_maker를 물고 있다 - 직접 돌린다.
    monkeypatch.setattr("src.workflows.runner.async_session_maker", test_session_maker)
    monkeypatch.setattr("src.db.session.async_session_maker", test_session_maker)


class TestResumeConsumptionPersists:
    async def test_resume_from_consumed_even_if_run_dies_right_after(
        self, super_admin_user, test_session: AsyncSession, test_session_maker, monkeypatch
    ) -> None:
        project = Project(
            title="재개 소비 영속",
            topic="철강",
            preset=None,
            config={"resume_from": "reviewing"},
            depth_mode="standard",
            owner_id=super_admin_user.id,
            status="cancelled",
        )
        test_session.add(project)
        await test_session.commit()

        async def _boom(session, pid, state):
            raise RuntimeError("pop 직후 사망 시나리오")

        # pop+커밋 바로 뒤 첫 재수화 지점에서 죽인다 - 커밋이 pop보다 뒤면 이 테스트가
        # resume_from 잔존으로 실패한다(사고 재현).
        monkeypatch.setattr(runner, "_rehydrate_section_plan", _boom)

        await runner._execute(project.id)

        async with test_session_maker() as fresh:
            cfg = (
                await fresh.execute(
                    text("SELECT config FROM projects WHERE id = :p"), {"p": str(project.id)}
                )
            ).scalar_one()
        assert "resume_from" not in (cfg or {})
