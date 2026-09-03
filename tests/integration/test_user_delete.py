"""계정 영구 삭제 — 비활성화와 별개, 보고서는 절대 잃지 않는다.

관리자는 비활성화만 가능했다(2026-08-10 요청). 흔적까지 지워야 하는 경우가 있어
영구 삭제를 열되, 실수로 보고서를 잃는 경로는 막는다.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.user import User
from src.infrastructure.auth import password_handler
from tests.conftest import auth_headers as _auth

pytestmark = pytest.mark.integration


async def _make_user(session: AsyncSession, email: str, *, active: bool = False) -> User:
    user = User(
        email=email,
        name="삭제 대상",
        role="viewer",
        is_active=active,
        password_hash=password_handler.hash_password("Smoke-2026!!aa"),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class TestPermanentDelete:
    async def test_deletes_inactive_user(
        self, test_client: AsyncClient, test_session: AsyncSession, super_admin_token: str
    ) -> None:
        user = await _make_user(test_session, "gone@example.com")
        resp = await test_client.delete(
            f"/api/v1/users/{user.id}/permanent", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 204, resp.text
        # 관찰 가능한 결과로 확인한다 - 테스트 세션은 자체 스냅샷을 들고 있어
        # 같은 트랜잭션에서 다시 조회하면 삭제 전 상태가 보일 수 있다.
        after = await test_client.get(f"/api/v1/users/{user.id}", headers=_auth(super_admin_token))
        assert after.status_code == 404

    async def test_active_user_must_be_deactivated_first(
        self, test_client: AsyncClient, test_session: AsyncSession, super_admin_token: str
    ) -> None:
        user = await _make_user(test_session, "active@example.com", active=True)
        resp = await test_client.delete(
            f"/api/v1/users/{user.id}/permanent", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "DEACTIVATE_FIRST"

    async def test_user_with_projects_is_refused(
        self, test_client: AsyncClient, test_session: AsyncSession, super_admin_token: str
    ) -> None:
        # 보고서를 잃는 사고를 막는다 - FK도 RESTRICT지만 이유를 사람 말로 돌려준다.
        user = await _make_user(test_session, "owner@example.com")
        test_session.add(Project(owner_id=user.id, title="보고서", topic="주제", status="created"))
        await test_session.commit()
        resp = await test_client.delete(
            f"/api/v1/users/{user.id}/permanent", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "USER_HAS_PROJECTS"
        assert "1건" in resp.json()["error"]["message"]

    async def test_cannot_delete_self(
        self, test_client: AsyncClient, super_admin_token: str, super_admin_user: User
    ) -> None:
        resp = await test_client.delete(
            f"/api/v1/users/{super_admin_user.id}/permanent", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "CANNOT_DELETE_SELF"
