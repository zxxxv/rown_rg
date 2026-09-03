"""공유 자산 — 목차 프리셋 3층 병합 · 라이브러리 '동료 공개' 폴더 · 가져오기(복제).

에이전트 공유(0041)와 같은 규약을 프리셋에도 연다(0042). 원칙 셋:
- 공개분은 **덮어쓰지 않고 뒤에 붙는다**. 남의 오버라이드가 내 자산을 조용히 바꾸면 안 된다.
- 남의 것은 **고칠 수 없다**(읽기 전용). 고치려면 가져와서(복제) 내 것으로 만든다.
- 가져온 복사본은 **공개를 승계하지 않는다**. 가져왔다고 내 이름으로 재공개되면 곤란하다.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import auth_headers as _auth

pytestmark = pytest.mark.asyncio

_OUTLINE = [{"title": "1장", "sections": [{"title": "1.1 절", "agents": []}]}]


def _find(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    for n in nodes:
        if n["id"] == node_id:
            return n
        if n["type"] == "folder":
            hit = _find(n["children"], node_id)
            if hit is not None:
                return hit
    return None


async def _make_preset(client: AsyncClient, token: str, name: str, *, public: bool) -> dict:
    resp = await client.post(
        "/api/v1/presets/personal",
        headers=_auth(token),
        json={
            "name": name,
            "description": "공유 테스트",
            "chapters": _OUTLINE,
            "is_public": public,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestPresetSharing:
    async def test_public_preset_appears_for_other_user(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        await _make_preset(test_client, worker_token, "공개 목차 구성", public=True)
        resp = await test_client.get("/api/v1/presets", headers=_auth(super_admin_token))
        hit = next(p for p in resp.json() if p["name"] == "공개 목차 구성")
        assert hit["scope"] == "shared"
        assert hit["owner_name"]

    async def test_private_preset_stays_invisible(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        await _make_preset(test_client, worker_token, "비공개 목차 구성", public=False)
        resp = await test_client.get("/api/v1/presets", headers=_auth(super_admin_token))
        assert "비공개 목차 구성" not in [p["name"] for p in resp.json()]

    async def test_owner_sees_own_preset_once(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        """공개 층에서 자기 것을 또 넣으면 선택지에 두 벌 뜬다."""
        await _make_preset(test_client, worker_token, "내가 공개한 구성", public=True)
        resp = await test_client.get("/api/v1/presets", headers=_auth(worker_token))
        names = [p["name"] for p in resp.json()]
        assert names.count("내가 공개한 구성") == 1
        mine = next(p for p in resp.json() if p["name"] == "내가 공개한 구성")
        assert mine["scope"] == "personal"

    async def test_shared_preset_skeleton_is_readable(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """골격을 못 읽으면 목차 편집기의 초기값으로 쓸 수 없다 = 공유가 무의미하다."""
        created = await _make_preset(test_client, worker_token, "골격 읽기", public=True)
        resp = await test_client.get(
            f"/api/v1/presets/{created['key']}", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["chapters"]

    async def test_shared_preset_can_start_a_project(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """남의 공개 프리셋으로 프로젝트를 못 만들면 공유가 성립하지 않는다."""
        created = await _make_preset(test_client, worker_token, "프로젝트 시작용", public=True)
        resp = await test_client.post(
            "/api/v1/projects",
            headers=_auth(super_admin_token),
            json={
                "title": "공유 프리셋 사용",
                "topic": "남이 공개한 프리셋으로 시작한다",
                "preset": created["key"],
                "config": {
                    "outline": {
                        "chapters": [
                            {"title": "1장", "sections": [{"title": "1.1", "analysts": []}]}
                        ]
                    }
                },
            },
        )
        assert resp.status_code == 201, resp.text

    async def test_others_cannot_edit_my_preset(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """읽기는 되고 쓰기는 막힌다 - 아니면 공유가 아니라 공용 편집이다."""
        created = await _make_preset(test_client, worker_token, "남이 못 고칠 구성", public=True)
        resp = await test_client.put(
            f"/api/v1/presets/personal/{created['id']}",
            headers=_auth(super_admin_token),
            json={"name": "가로챈 이름", "description": None, "chapters": _OUTLINE},
        )
        assert resp.status_code == 404


class TestImport:
    async def test_import_preset_makes_a_private_copy(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        created = await _make_preset(test_client, worker_token, "가져갈 구성", public=True)
        resp = await test_client.post(
            f"/api/v1/presets/personal/import/{created['id']}", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 201, resp.text
        copy = resp.json()
        assert copy["id"] != created["id"]
        # 가져왔다고 내 이름으로 자동 재공개되면 곤란하다
        assert copy["is_public"] is False

    async def test_import_agent_drops_base_ref_and_public(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """남이 시스템 에이전트를 덮어쓴 변형본이라도 내 시스템 항목까지 갈아끼우면 안 된다."""
        created = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "덮어쓴 변형본",
                "content": "너는 내 방식의 STEEP 분석가다.",
                "base_ref": "a01",
                "is_public": True,
            },
        )
        assert created.status_code == 201, created.text
        resp = await test_client.post(
            f"/api/v1/prompts/personal/import/{created.json()['id']}",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 201, resp.text
        copy = resp.json()
        assert copy["base_ref"] is None
        assert copy["is_public"] is False
        assert copy["content"] == "너는 내 방식의 STEEP 분석가다."

    async def test_import_twice_renames_the_copy(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """이름이 겹치면 유니크 제약에 걸려 500이 난다 - 사본 이름을 붙여 피한다."""
        created = await _make_preset(test_client, worker_token, "두 번 가져갈 구성", public=True)
        first = await test_client.post(
            f"/api/v1/presets/personal/import/{created['id']}", headers=_auth(super_admin_token)
        )
        second = await test_client.post(
            f"/api/v1/presets/personal/import/{created['id']}", headers=_auth(super_admin_token)
        )
        assert first.status_code == 201
        assert second.status_code == 201, second.text
        assert first.json()["name"] != second.json()["name"]

    async def test_cannot_import_private_asset(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        created = await _make_preset(test_client, worker_token, "비공개 못 가져감", public=False)
        resp = await test_client.post(
            f"/api/v1/presets/personal/import/{created['id']}", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 404


class TestLibraryTree:
    async def test_my_presets_appear_under_personal_root(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        await _make_preset(test_client, worker_token, "트리에 뜰 구성", public=False)
        tree = (await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))).json()
        folder = _find(tree["tree"], "me-presets")
        assert folder is not None
        assert "트리에 뜰 구성" in [c["name"] for c in folder["children"]]

    async def test_system_presets_are_listed_read_only(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        tree = (await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))).json()
        folder = _find(tree["tree"], "sys-presets")
        assert folder is not None and folder["children"]
        assert all(c["prompt"]["editable"] is False for c in folder["children"])

    async def test_shared_folder_shows_others_public_assets(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        await _make_preset(test_client, worker_token, "동료 공개 구성", public=True)
        tree = (
            await test_client.get("/api/v1/library/tree", headers=_auth(super_admin_token))
        ).json()
        folder = _find(tree["tree"], "shared-presets")
        assert folder is not None
        hit = next(c for c in folder["children"] if c["name"] == "동료 공개 구성")
        ref = hit["prompt"]
        assert ref["scope"] == "shared"
        assert ref["editable"] is False  # 남의 것은 라이브러리에서 못 고친다
        assert ref["importable"] is True  # 대신 가져올 수 있다
        assert ref["owner_name"]

    async def test_my_public_asset_is_not_in_shared_folder(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        """자기 것이 '동료 공개'에도 뜨면 같은 자산이 트리에 두 번 보인다."""
        await _make_preset(test_client, worker_token, "내가 공개한 트리 구성", public=True)
        tree = (await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))).json()
        folder = _find(tree["tree"], "shared-presets")
        assert folder is not None
        assert "내가 공개한 트리 구성" not in [c["name"] for c in folder["children"]]
