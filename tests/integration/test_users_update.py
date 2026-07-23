"""PATCH /users/{id} 응답 직렬화 회귀 — 활성 토글·역할 변경.

과거 flush 후 refresh 누락으로 updated_at(server onupdate)을 직렬화하다
MissingGreenlet(500)이 났다. 이 테스트가 그 경로를 지킨다.
"""

from __future__ import annotations

from httpx import AsyncClient

from src.db.models.user import User


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestUserUpdate:
    async def test_toggle_active(
        self, test_client: AsyncClient, super_admin_token: str, worker_user: User
    ) -> None:
        resp = await test_client.patch(
            f"/api/v1/users/{worker_user.id}",
            headers=_auth(super_admin_token),
            json={"is_active": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_active"] is False
        assert body["updated_at"]  # 직렬화 성공(예전 500 지점)

    async def test_change_role(
        self, test_client: AsyncClient, super_admin_token: str, worker_user: User
    ) -> None:
        resp = await test_client.patch(
            f"/api/v1/users/{worker_user.id}",
            headers=_auth(super_admin_token),
            json={"role": "admin"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"

    async def test_admin_cannot_touch_super_admin(
        self, test_client: AsyncClient, admin_token: str, super_admin_user: User
    ) -> None:
        resp = await test_client.patch(
            f"/api/v1/users/{super_admin_user.id}",
            headers=_auth(admin_token),
            json={"is_active": False},
        )
        assert resp.status_code == 403

    async def test_cannot_demote_self(
        self, test_client: AsyncClient, super_admin_token: str, super_admin_user: User
    ) -> None:
        resp = await test_client.patch(
            f"/api/v1/users/{super_admin_user.id}",
            headers=_auth(super_admin_token),
            json={"role": "viewer"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "CANNOT_CHANGE_OWN_ROLE"

    async def test_cannot_deactivate_self(
        self, test_client: AsyncClient, super_admin_token: str, super_admin_user: User
    ) -> None:
        resp = await test_client.patch(
            f"/api/v1/users/{super_admin_user.id}",
            headers=_auth(super_admin_token),
            json={"is_active": False},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "CANNOT_DEACTIVATE_SELF"

    async def test_cannot_promote_to_second_super_admin(
        self,
        test_client: AsyncClient,
        super_admin_token: str,
        super_admin_user: User,
        worker_user: User,
    ) -> None:
        # super_admin_user가 이미 있으므로 worker를 super_admin으로 승격 불가.
        resp = await test_client.patch(
            f"/api/v1/users/{worker_user.id}",
            headers=_auth(super_admin_token),
            json={"role": "super_admin"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "SUPER_ADMIN_EXISTS"

    async def test_cannot_register_second_super_admin(
        self, test_client: AsyncClient, super_admin_token: str, super_admin_user: User
    ) -> None:
        resp = await test_client.post(
            "/api/v1/auth/register",
            headers=_auth(super_admin_token),
            json={
                "email": "second-super@test.com",
                "password": "ValidPass123!@",
                "name": "Second Super",
                "role": "super_admin",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "SUPER_ADMIN_EXISTS"
