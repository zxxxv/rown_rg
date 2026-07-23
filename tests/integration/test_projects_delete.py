"""프로젝트 삭제(DELETE /projects/{id}) + owner_name 표시 통합 테스트."""

from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_project(test_client: AsyncClient, token: str) -> dict:
    resp = await test_client.post(
        "/api/v1/projects",
        headers=_auth(token),
        json={
            "title": "삭제 테스트",
            "topic": "삭제 대상 프로젝트",
            "preset": None,
            "config": {},
            "depth_mode": "standard",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestProjectDelete:
    async def test_read_includes_owner_name(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        body = await _create_project(test_client, super_admin_token)
        assert body["owner_name"] == "Super Admin"

    async def test_delete_rejects_non_completed(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        body = await _create_project(test_client, super_admin_token)
        resp = await test_client.delete(
            f"/api/v1/projects/{body['id']}", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PROJECT_NOT_DELETABLE"

    async def test_delete_completed_project(
        self,
        test_client: AsyncClient,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        body = await _create_project(test_client, super_admin_token)
        project = await test_session.get(Project, UUID(body["id"]))
        assert project is not None
        project.status = "completed"
        await test_session.commit()

        resp = await test_client.delete(
            f"/api/v1/projects/{body['id']}", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 204

        gone = await test_client.get(
            f"/api/v1/projects/{body['id']}", headers=_auth(super_admin_token)
        )
        assert gone.status_code == 404

    async def test_delete_forbidden_for_other_worker(
        self,
        test_client: AsyncClient,
        super_admin_token: str,
        worker_token: str,
        test_session: AsyncSession,
    ) -> None:
        body = await _create_project(test_client, super_admin_token)
        project = await test_session.get(Project, UUID(body["id"]))
        assert project is not None
        project.status = "completed"
        await test_session.commit()

        resp = await test_client.delete(
            f"/api/v1/projects/{body['id']}", headers=_auth(worker_token)
        )
        assert resp.status_code == 403
