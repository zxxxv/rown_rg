"""보고서 생성 취소 API 통합 테스트 — 상태 검증 + 비실행 상태 즉시 취소.

프로젝트는 세션으로 직접 삽입한다(생성 API는 config.outline 필수라 취소 테스트엔 과함).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.user import User
from tests.conftest import auth_headers as _auth


async def _insert_project(session: AsyncSession, owner_id: uuid.UUID, status: str) -> uuid.UUID:
    proj = Project(
        title="취소 테스트",
        topic="테스트 주제",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status=status,
    )
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return proj.id


class TestCancelApi:
    async def test_created_cannot_cancel(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        pid = await _insert_project(test_session, worker_user.id, "created")
        resp = await test_client.post(f"/api/v1/projects/{pid}/cancel", headers=_auth(worker_token))
        assert resp.status_code == 422  # 아직 시작 안 함

    async def test_running_state_finalizes_to_cancelled(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        # 실행 없이 진행 중 상태로 삽입 — 테스트 프로세스엔 러너 태스크가 없어 비실행 분기.
        pid = await _insert_project(test_session, worker_user.id, "writing")
        resp = await test_client.post(f"/api/v1/projects/{pid}/cancel", headers=_auth(worker_token))
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "cancelled"

        proj = await test_session.get(Project, pid)
        assert proj is not None
        await test_session.refresh(proj)
        assert proj.status == "cancelled"

    async def test_completed_cannot_cancel(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        pid = await _insert_project(test_session, worker_user.id, "completed")
        resp = await test_client.post(f"/api/v1/projects/{pid}/cancel", headers=_auth(worker_token))
        assert resp.status_code == 422  # 종료된 프로젝트

    async def test_cancel_requires_ownership(
        self,
        test_client: AsyncClient,
        viewer_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        pid = await _insert_project(test_session, worker_user.id, "writing")
        # 남(viewer)의 취소 시도 → 403
        resp = await test_client.post(f"/api/v1/projects/{pid}/cancel", headers=_auth(viewer_token))
        assert resp.status_code == 403
