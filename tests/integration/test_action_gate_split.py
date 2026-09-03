"""행동은 게이트와 분리된다 — 자료 더 모으기가 검토 상태를 소비하지 않는다.

예전엔 이 요청이 게이트 결정 API(/decide {action: collect_more})를 통해 나갔다.
그래서 ①게이트가 없으면 보낼 통로가 없어 버튼이 사라지고 ②누르는 순간 검토 상태가
함께 소비됐다 — "자료 10건 더"와 "검토를 마쳤다"가 한 몸이었다(2026-08-26 분리).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.user import User
from tests.conftest import auth_headers as _auth


async def _project(session: AsyncSession, owner_id: uuid.UUID, status: str) -> uuid.UUID:
    proj = Project(
        title="행동 분리",
        topic="주제",
        config={},
        status=status,
        depth_mode="full_report",
        owner_id=owner_id,
    )
    session.add(proj)
    await session.commit()
    return proj.id


@pytest.fixture(autouse=True)
def _no_real_collect(monkeypatch):
    """실제 수집은 돌리지 않는다 — 이 테스트의 관심은 '누를 수 있는가'와 '게이트가 살아남는가'."""
    from src.workflows import runner

    monkeypatch.setattr(runner, "_spawn_collect_more", lambda *a, **k: True)


class TestCollectMoreIsGateIndependent:
    @pytest.mark.parametrize("status", ["completed", "reviewing", "indexing"])
    async def test_callable_without_any_gate(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        status: str,
    ) -> None:
        """게이트가 없어도 202 — 자료를 더 모으는 일에 '검토 대기'가 전제일 이유가 없다."""
        pid = await _project(test_session, worker_user.id, status)
        resp = await test_client.post(
            f"/api/v1/projects/{pid}/collect-more", headers=_auth(worker_token)
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["started"] is True
        # 멈춰 선 파이프라인이 없으니 게이트를 새로 만들지 않는다 — 만들면 완료된
        # 보고서가 난데없이 '검토 대기'로 바뀐다.
        assert body["reopen_gate"] is False

    async def test_does_not_consume_a_pending_gate(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        """검토 게이트가 열려 있어도 추가 검색이 그것을 닫지 않는다."""
        from src.core.types import ReviewGate
        from src.db.models.review_point import ReviewPoint

        pid = await _project(test_session, worker_user.id, "researching")
        test_session.add(
            ReviewPoint(
                project_id=pid,
                gate=ReviewGate.SOURCE_POOL.value,
                payload={"message": "검토하세요"},
                status="pending",
            )
        )
        await test_session.commit()

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/collect-more", headers=_auth(worker_token)
        )
        assert resp.status_code == 202, resp.text
        # 첫 런의 검토 중이므로 보충 후 다시 판단을 기다린다.
        assert resp.json()["reopen_gate"] is True

        progress = await test_client.get(
            f"/api/v1/projects/{pid}/progress", headers=_auth(worker_token)
        )
        assert progress.json()["pending_gate"] is not None, "추가 검색이 게이트를 소비하면 안 된다"

    async def test_viewer_cannot_collect(
        self,
        test_client: AsyncClient,
        viewer_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        pid = await _project(test_session, worker_user.id, "completed")
        resp = await test_client.post(
            f"/api/v1/projects/{pid}/collect-more", headers=_auth(viewer_token)
        )
        assert resp.status_code in (403, 404), resp.text
