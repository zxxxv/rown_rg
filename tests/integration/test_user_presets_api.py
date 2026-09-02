"""개인 목차 프리셋 API — 저장·병합 노출·골격 로드·소유자 격리·생성 검증.

같은 보고서 구성으로 여러 정책을 분석하는 용례(2026-08-12 QA 2번의 확장):
목차 편집기 구성을 "u:<uuid>" 키로 저장하고, 시스템 프리셋과 같은 코드 경로로
불러온다. 시스템 카탈로그(파일)는 그대로 단일 진실이고 개인은 DB 오버레이.
"""

from __future__ import annotations

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_CHAPTERS = [
    {
        "title": "글로벌 RE100",
        "sections": [
            {
                "title": "동향 분석",
                "direction": "글로벌 RE100 확산 추이 분석",
                "key_points": ["가입 기업 추이"],
                "agents": ["STEEP분석"],
            },
            {"title": "시사점", "direction": "", "key_points": [], "agents": []},
        ],
    },
]


async def _create(test_client: AsyncClient, token: str, name: str = "정책분석 구성") -> dict:
    resp = await test_client.post(
        "/api/v1/presets/personal",
        headers=_auth(token),
        json={"name": name, "description": "정책 비교용", "chapters": _CHAPTERS},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestPersonalPresetCrud:
    async def test_create_and_appears_in_merged_catalog(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        created = await _create(test_client, worker_token)
        assert created["key"].startswith("u:")
        assert created["n_chapters"] == 1
        assert created["n_sections"] == 2

        listed = await test_client.get("/api/v1/presets", headers=_auth(worker_token))
        rows = listed.json()
        mine = [p for p in rows if p.get("scope") == "personal"]
        assert [p["id"] for p in mine] == [created["key"]]
        # 시스템 프리셋도 여전히 함께 온다(병합이지 대체가 아니다)
        assert any(p.get("scope") == "system" for p in rows)

    async def test_detail_serves_same_shape_as_system(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        """ "u:" 상세가 시스템 프리셋과 같은 모양이어야 프론트 로드 코드가 갈라지지 않는다."""
        created = await _create(test_client, worker_token)
        resp = await test_client.get(
            f"/api/v1/presets/{created['key']}", headers=_auth(worker_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == created["key"]
        assert body["chapters"][0]["title"] == "글로벌 RE100"
        assert body["chapters"][0]["sections"][0]["agents"] == ["STEEP분석"]

    async def test_update_and_delete(self, test_client: AsyncClient, worker_token: str) -> None:
        created = await _create(test_client, worker_token)
        put = await test_client.put(
            f"/api/v1/presets/personal/{created['id']}",
            headers=_auth(worker_token),
            json={"name": "개정 구성", "description": None, "chapters": _CHAPTERS},
        )
        assert put.status_code == 200, put.text
        assert put.json()["name"] == "개정 구성"

        deleted = await test_client.delete(
            f"/api/v1/presets/personal/{created['id']}", headers=_auth(worker_token)
        )
        assert deleted.status_code == 204
        gone = await test_client.get(
            f"/api/v1/presets/{created['key']}", headers=_auth(worker_token)
        )
        assert gone.status_code == 404

    async def test_duplicate_name_rejected(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        await _create(test_client, worker_token, name="중복 이름")
        resp = await test_client.post(
            "/api/v1/presets/personal",
            headers=_auth(worker_token),
            json={"name": "중복 이름", "chapters": _CHAPTERS},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "DUPLICATE_PRESET_NAME"

    async def test_duplicate_name_overwrite_replaces_in_place(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        """overwrite=true는 같은 이름의 기존 행을 교체한다 - id(u: 키)가 유지돼야
        복제("(2)(3)")가 안 쌓이고, 이 프리셋을 가리키던 참조도 안 끊긴다."""
        created = await _create(test_client, worker_token, name="덮어쓸 이름")
        resp = await test_client.post(
            "/api/v1/presets/personal",
            headers=_auth(worker_token),
            json={
                "name": "덮어쓸 이름",
                "description": "교체된 설명",
                "chapters": _CHAPTERS,
                "overwrite": True,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] == created["id"]
        assert body["description"] == "교체된 설명"
        listed = await test_client.get("/api/v1/presets", headers=_auth(worker_token))
        mine = [p for p in listed.json() if p.get("scope") == "personal"]
        assert [p["name"] for p in mine] == ["덮어쓸 이름"]

    async def test_empty_sections_rejected(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/presets/personal",
            headers=_auth(worker_token),
            json={"name": "빈 구성", "chapters": [{"title": "장만 있음", "sections": []}]},
        )
        assert resp.status_code == 422

    async def test_owner_isolation(
        self, test_client: AsyncClient, worker_token: str, viewer_token: str
    ) -> None:
        """비관리자 동료에게는 목록에도, 상세에도 없다.

        예전엔 super_admin_token을 '다른 사용자' 대역으로 썼는데, 2de4ad3에서 관리자에게
        남의 비공개 프리셋 **열람**을 의도적으로 열면서 그 대역이 더는 성립하지 않는다
        (관리자 계약은 아래 별도 테스트가 맡는다).
        """
        created = await _create(test_client, worker_token)
        listed = await test_client.get("/api/v1/presets", headers=_auth(viewer_token))
        assert all(p["id"] != created["key"] for p in listed.json())
        detail = await test_client.get(
            f"/api/v1/presets/{created['key']}", headers=_auth(viewer_token)
        )
        assert detail.status_code == 404

    async def test_admin_can_read_but_not_list_others_preset(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """관리자는 남의 비공개 프리셋을 **열람**할 수 있다(2de4ad3 대리 조작).

        다만 목록 격리는 관리자에게도 그대로다 — 열람을 연 것이 소유자 격리 전체를
        무너뜨린 것은 아니라는 게 이 계약의 핵심이다.
        """
        created = await _create(test_client, worker_token)
        listed = await test_client.get("/api/v1/presets", headers=_auth(super_admin_token))
        assert all(p["id"] != created["key"] for p in listed.json()), (
            "남의 비공개 프리셋이 관리자 목록에 뜨면 안 된다"
        )
        detail = await test_client.get(
            f"/api/v1/presets/{created['key']}", headers=_auth(super_admin_token)
        )
        assert detail.status_code == 200, detail.text


class TestProjectCreateWithPersonalPreset:
    async def test_create_project_accepts_owned_personal_key(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        created = await _create(test_client, worker_token)
        resp = await test_client.post(
            "/api/v1/projects",
            headers=_auth(worker_token),
            json={
                "title": "한국형 RE100 분석",
                "topic": "한국형 RE100",
                "preset": created["key"],
                "config": {
                    "outline": {
                        "chapters": [
                            {
                                "title": "1장",
                                "sections": [
                                    {
                                        "title": "개요",
                                        "direction": "",
                                        "key_points": [],
                                        "analysts": [],
                                    }
                                ],
                            }
                        ]
                    }
                },
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["preset"] == created["key"]

    async def test_create_project_rejects_foreign_personal_key(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        created = await _create(test_client, worker_token)
        resp = await test_client.post(
            "/api/v1/projects",
            headers=_auth(super_admin_token),
            json={
                "title": "남의 프리셋",
                "topic": "격리 확인",
                "preset": created["key"],
                "config": {
                    "outline": {
                        "chapters": [
                            {
                                "title": "1장",
                                "sections": [
                                    {
                                        "title": "개요",
                                        "direction": "",
                                        "key_points": [],
                                        "analysts": [],
                                    }
                                ],
                            }
                        ]
                    }
                },
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "UNKNOWN_PRESET"
