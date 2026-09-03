"""목차를 고치면 설계도 따라 바뀐다 — 남은 분담이 거짓 지시가 되던 것(2026-08-27).

소재 분담(_design_plan)은 절 번호를 **문자열로** 박아 둔다("…는 1.2절 소관, 참조 한
문장으로 대체하라"). 절 번호는 배열 위치에서 파생되므로 절을 하나 끼워 넣으면 번호가
밀리는데, 그 문자열은 그대로 남는다 — 작성기가 엉뚱한 절을 가리키는 지시를 따른다.

종전 동작은 가장 나쁜 중간이었다: 사라진 절의 분담만 버리고 살아남은 절 것은 그대로 뒀다.
이제 통째로 버리고 새 목차로 다시 뽑는다. 수집·작성은 **따라 돌지 않는다**(절당 실측
$0.4~1.3) — 그건 '미반영'으로 표시하고 사람이 고른다.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.user import User
from tests.conftest import auth_headers as _auth
from tests.fixtures.builders import completed_project as _completed_project
from tests.fixtures.builders import drift_outline as _outline


class TestDesignRefreshOnOutlineChange:
    async def test_stale_ownership_is_dropped_wholesale(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """살아남은 절의 분담도 버린다 - 번호가 밀리면 그 문구가 거짓말이 된다."""
        from src.api.routers import projects as projects_router

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        proj = await test_session.get(Project, pid)
        proj.config = {
            **proj.config,
            "_design_plan": {
                str(sid): {
                    "owns": "총사업비 산정",
                    "foreign_topics": "수요 추정(2.1절 소관)",
                }
            },
        }
        await test_session.commit()

        spawned: list[uuid.UUID] = []
        monkeypatch.setattr(projects_router, "is_running", lambda _pid: False, raising=False)
        import src.workflows.runner as runner

        monkeypatch.setattr(runner, "spawn_design_refresh", lambda p: spawned.append(p) or True)

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )
        assert resp.status_code == 200, resp.text

        await test_session.refresh(proj)
        assert "_design_plan" not in (proj.config or {}), (
            "옛 분담이 남으면 엉뚱한 절을 가리키는 지시가 작성기에 내려간다"
        )
        assert spawned == [pid], "설계 재계산이 예약되지 않았다"

    async def test_body_is_not_rewritten_automatically(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """설계는 따라 바뀌어도 **본문은 안 건드린다** - 절당 실측 $0.4~1.3짜리다.

        바뀐 사실은 '미반영'으로 드러나고, 다시 쓸지는 사람이 고른다.
        """
        from src.api.routers import projects as projects_router
        from src.db.models.section import Section

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")

        async def _boom(*_a, **_k):
            raise AssertionError("목차 수정이 본문 재작성을 불렀다")

        monkeypatch.setattr(projects_router, "_section_rewriter", _boom)
        import src.workflows.runner as runner

        monkeypatch.setattr(runner, "spawn_design_refresh", lambda _p: True)

        await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )

        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다."
        drift = (
            await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        ).json()
        assert drift["n_plan_changed"] == 1, "바뀐 사실은 미반영으로 드러나야 한다"
