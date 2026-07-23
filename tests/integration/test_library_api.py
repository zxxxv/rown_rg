"""자료 라이브러리 API 통합 테스트 — 폴더/업로드/트리/권한/삭제."""

from __future__ import annotations

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_folder(
    test_client: AsyncClient, token: str, name: str, parent_id: str | None = None
) -> dict:
    resp = await test_client.post(
        "/api/v1/library/folders",
        headers=_auth(token),
        json={"name": name, "parent_id": parent_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _upload(
    test_client: AsyncClient, token: str, filename: str, parent_id: str | None = None
) -> dict:
    data = {}
    if parent_id:
        data["parent_id"] = parent_id
    resp = await test_client.post(
        "/api/v1/library/files",
        headers=_auth(token),
        files={"file": (filename, b"hello library", "text/plain")},
        data=data,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestLibraryApi:
    async def test_folder_upload_and_tree(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        folder = await _create_folder(test_client, worker_token, "공용 자료")
        uploaded = await _upload(test_client, worker_token, "지침.txt", folder["id"])
        assert uploaded["type"] == "file"
        assert uploaded["file_meta"]["source_kind"] == "upload"
        assert uploaded["file_meta"]["registered_by"] == "Worker"

        tree = await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))
        assert tree.status_code == 200
        roots = tree.json()["tree"]
        target = next(n for n in roots if n["id"] == folder["id"])
        assert target["type"] == "folder"
        assert [c["name"] for c in target["children"]] == ["지침.txt"]

    async def test_projects_appear_as_named_folders(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        created = await test_client.post(
            "/api/v1/projects",
            headers=_auth(super_admin_token),
            json={
                "title": "고령화 대응 보고서",
                "topic": "지방 인구 감소",
                "preset": None,
                "config": {},
                "depth_mode": "standard",
            },
        )
        assert created.status_code == 201, created.text

        tree = await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        roots = tree.json()["tree"]
        group = next((n for n in roots if n["id"] == "projects-root"), None)
        assert group is not None, roots
        assert group["name"] == "프로젝트"
        assert "고령화 대응 보고서" in [c["name"] for c in group["children"]]

    async def test_viewer_cannot_write(self, test_client: AsyncClient, viewer_token: str) -> None:
        resp = await test_client.post(
            "/api/v1/library/folders",
            headers=_auth(viewer_token),
            json={"name": "금지 폴더", "parent_id": None},
        )
        assert resp.status_code == 403

    async def test_visibility_filters_tree(
        self,
        test_client: AsyncClient,
        super_admin_token: str,
        worker_token: str,
    ) -> None:
        secret = await _upload(test_client, super_admin_token, "기밀.txt")
        patch = await test_client.patch(
            f"/api/v1/library/nodes/{secret['id']}/visibility",
            headers=_auth(super_admin_token),
            json={"visible_to_roles": ["admin", "super_admin"]},
        )
        assert patch.status_code == 200

        worker_tree = await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))
        worker_ids = [n["id"] for n in worker_tree.json()["tree"]]
        assert secret["id"] not in worker_ids

        admin_tree = await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        admin_ids = [n["id"] for n in admin_tree.json()["tree"]]
        assert secret["id"] in admin_ids

    async def test_download_roundtrip(self, test_client: AsyncClient, worker_token: str) -> None:
        uploaded = await _upload(test_client, worker_token, "본문.txt")
        resp = await test_client.get(
            f"/api/v1/library/files/{uploaded['id']}/download",
            headers=_auth(worker_token),
        )
        assert resp.status_code == 200
        assert resp.content == b"hello library"

    async def test_delete_rules(
        self,
        test_client: AsyncClient,
        worker_token: str,
        viewer_token: str,
        super_admin_token: str,
    ) -> None:
        folder = await _create_folder(test_client, worker_token, "삭제 대상")
        await _upload(test_client, worker_token, "안쪽.txt", folder["id"])

        # viewer는 삭제 불가(쓰기 역할 아님)
        denied = await test_client.delete(
            f"/api/v1/library/nodes/{folder['id']}", headers=_auth(viewer_token)
        )
        assert denied.status_code == 403

        # 생성자 본인은 폴더째 삭제 가능(하위 포함)
        ok = await test_client.delete(
            f"/api/v1/library/nodes/{folder['id']}", headers=_auth(worker_token)
        )
        assert ok.status_code == 204

        tree = await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        assert folder["id"] not in [n["id"] for n in tree.json()["tree"]]
