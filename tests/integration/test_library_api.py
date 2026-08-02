"""자료 라이브러리 API 통합 테스트 — 개인/회사 2탑레벨·프로젝트 가상뷰·권한·삭제.

트리 구조: tree = [개인 루트("me"), 회사 공유("company")].
- 회사 공유 최상위 실노드 → company.children
- 개인 업로드(is_personal) → me > me-files
- 내 프로젝트(소유자 스코프) → me > me-projects > <프로젝트명>/(완성본·AI수집·업로드·참조)
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _find(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    """트리 전체에서 id로 노드를 재귀 탐색."""
    for n in nodes:
        if n["id"] == node_id:
            return n
        if n["type"] == "folder":
            hit = _find(n["children"], node_id)
            if hit is not None:
                return hit
    return None


def _all_file_ids(nodes: list[dict[str, Any]]) -> list[str]:
    """트리에 실제로 노출된 파일 노드 id 전부(가시성 필터 반영)."""
    ids: list[str] = []
    for n in nodes:
        if n["type"] == "file":
            ids.append(n["id"])
        else:
            ids.extend(_all_file_ids(n["children"]))
    return ids


async def _create_folder(
    test_client: AsyncClient,
    token: str,
    name: str,
    parent_id: str | None = None,
    is_personal: bool = False,
) -> dict:
    resp = await test_client.post(
        "/api/v1/library/folders",
        headers=_auth(token),
        json={"name": name, "parent_id": parent_id, "is_personal": is_personal},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _upload(
    test_client: AsyncClient,
    token: str,
    filename: str,
    parent_id: str | None = None,
    is_personal: bool = False,
) -> dict:
    data: dict[str, str] = {"is_personal": "true" if is_personal else "false"}
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
        # 최상위(is_personal=False) → 회사 공유 아래에 실노드로 붙는다.
        folder = await _create_folder(test_client, worker_token, "공용 자료")
        uploaded = await _upload(test_client, worker_token, "지침.txt", folder["id"])
        assert uploaded["type"] == "file"
        assert uploaded["file_meta"]["source_kind"] == "upload"
        assert uploaded["file_meta"]["registered_by"] == "Worker"

        tree = await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))
        assert tree.status_code == 200
        roots = tree.json()["tree"]
        # 2탑레벨: 개인 루트 + 회사 공유
        assert [n["id"] for n in roots] == ["me", "company"]

        company = _find(roots, "company")
        assert company is not None
        target = _find(company["children"], folder["id"])
        assert target is not None
        assert target["type"] == "folder"
        assert [c["name"] for c in target["children"]] == ["지침.txt"]

    async def test_projects_appear_under_personal_root(
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
        me_projects = _find(roots, "me-projects")
        assert me_projects is not None
        assert me_projects["name"] == "프로젝트"
        proj = next((c for c in me_projects["children"] if c["name"] == "고령화 대응 보고서"), None)
        assert proj is not None, me_projects
        # 프로젝트 폴더는 AI수집/업로드/참조 3개 가상 하위 폴더를 갖는다(가상=읽기전용).
        assert proj["virtual"] is True
        sub_names = [c["name"] for c in proj["children"]]
        assert "AI 수집 자료" in sub_names
        assert "사용자 업로드" in sub_names
        assert "라이브러리 참조" in sub_names

    async def test_projects_are_owner_scoped(
        self,
        test_client: AsyncClient,
        super_admin_token: str,
        worker_token: str,
    ) -> None:
        """내 프로젝트만 라이브러리에 보인다 — 관리자도 타인 프로젝트는 안 보임.

        (라이브러리와 프로젝트 목록 기본 scope=mine 가시성 일치)
        """
        created = await test_client.post(
            "/api/v1/projects",
            headers=_auth(worker_token),
            json={
                "title": "워커의 비밀 프로젝트",
                "topic": "테스트",
                "preset": None,
                "config": {},
                "depth_mode": "standard",
            },
        )
        assert created.status_code == 201, created.text

        # super_admin의 라이브러리엔 worker 프로젝트가 없어야 한다.
        tree = await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        me_projects = _find(tree.json()["tree"], "me-projects")
        assert me_projects is not None
        assert "워커의 비밀 프로젝트" not in [c["name"] for c in me_projects["children"]]

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
        assert secret["id"] not in _all_file_ids(worker_tree.json()["tree"])

        admin_tree = await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        assert secret["id"] in _all_file_ids(admin_tree.json()["tree"])

    async def test_personal_upload_is_private(
        self,
        test_client: AsyncClient,
        worker_token: str,
        super_admin_token: str,
    ) -> None:
        """is_personal 업로드는 내 자료에만 들어가고, 관리자도 볼 수 없다("개인=나만")."""
        personal = await _upload(test_client, worker_token, "개인메모.txt", is_personal=True)

        worker_tree = await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))
        me_files = _find(worker_tree.json()["tree"], "me-files")
        assert me_files is not None
        assert personal["id"] in [c["id"] for c in me_files["children"]]

        # 관리자 트리에는 타인의 개인 자료가 아예 없다.
        admin_tree = await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        assert personal["id"] not in _all_file_ids(admin_tree.json()["tree"])

    async def test_personal_download_denied_for_others(
        self,
        test_client: AsyncClient,
        worker_token: str,
        super_admin_token: str,
    ) -> None:
        """개인 파일은 소유자만 다운로드 가능(관리자도 404)."""
        personal = await _upload(test_client, worker_token, "내파일.txt", is_personal=True)

        owner = await test_client.get(
            f"/api/v1/library/files/{personal['id']}/download", headers=_auth(worker_token)
        )
        assert owner.status_code == 200
        assert owner.content == b"hello library"

        other = await test_client.get(
            f"/api/v1/library/files/{personal['id']}/download", headers=_auth(super_admin_token)
        )
        assert other.status_code == 404

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
        assert _find(tree.json()["tree"], folder["id"]) is None
