"""뷰어(열람 전용) 권한 경계 — 쓰기 엔드포인트는 명시적 사유와 함께 거절한다.

전에는 쓰기 엔드포인트가 역할을 안 봐서 뷰어(SSO JIT 기본 역할)도 프로젝트를 만들 수
있었다. 막을 때는 조용한 실패가 아니라 사람이 읽고 행동할 이유를 준다(2026-08-14
사용자 결정): code=WRITE_ROLE_REQUIRED + "관리자에게 권한 상향을 문의하세요".
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import auth_headers as _auth


def _project_payload() -> dict:
    return {
        "title": "권한 테스트",
        "topic": "뷰어 차단 검증",
        "preset": None,
        "config": {
            "outline": {
                "chapters": [
                    {
                        "title": "1장",
                        "sections": [
                            {"title": "개요", "direction": "", "key_points": [], "analysts": []}
                        ],
                    }
                ]
            }
        },
        "depth_mode": "standard",
    }


class TestViewerReadOnly:
    async def test_viewer_cannot_create_project(
        self, test_client: AsyncClient, viewer_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/projects", headers=_auth(viewer_token), json=_project_payload()
        )
        assert resp.status_code == 403, resp.text
        err = resp.json()["error"]
        assert err["code"] == "WRITE_ROLE_REQUIRED"
        # 막힌 이유가 사람이 읽을 한국어로 온다 - 프론트 토스트가 그대로 보여준다.
        assert "관리자" in err["message"]

    async def test_viewer_cannot_run_or_upload(
        self,
        test_client: AsyncClient,
        viewer_token: str,
        super_admin_token: str,
    ) -> None:
        # 실행·업로드는 실재하는 프로젝트로 검증 - /run은 비용 한도 의존성이 프로젝트를
        # 먼저 조회하므로, 없는 id로는 404가 앞선다(가드 검증이 안 된다).
        created = await test_client.post(
            "/api/v1/projects", headers=_auth(super_admin_token), json=_project_payload()
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]

        # /run은 비용 한도 의존성의 소유권 검사(FORBIDDEN)가 역할 가드보다 먼저 걸릴 수
        # 있다 - 남의 프로젝트인 이 시나리오에선 코드가 아니라 차단(403) 자체를 검증한다.
        # (역할 가드의 고유 코드는 아래 upload가 검증 - 그쪽은 의존성이 가드뿐이다.)
        run = await test_client.post(f"/api/v1/projects/{pid}/run", headers=_auth(viewer_token))
        assert run.status_code == 403, run.text

        upload = await test_client.post(
            f"/api/v1/projects/{pid}/sources/upload",
            headers=_auth(viewer_token),
            files={"file": ("a.txt", b"content", "text/plain")},
        )
        assert upload.status_code == 403, upload.text
        assert upload.json()["error"]["code"] == "WRITE_ROLE_REQUIRED"

    async def test_viewer_cannot_write_prompts(
        self, test_client: AsyncClient, viewer_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(viewer_token),
            json={"name": "테스트", "content": "본문"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "WRITE_ROLE_REQUIRED"

    async def test_viewer_can_still_read(self, test_client: AsyncClient, viewer_token: str) -> None:
        # 열람은 그대로 - 목록·라이브러리 트리가 200으로 열린다.
        assert (
            await test_client.get("/api/v1/projects", headers=_auth(viewer_token))
        ).status_code == 200
        assert (
            await test_client.get("/api/v1/library/tree", headers=_auth(viewer_token))
        ).status_code == 200

    async def test_worker_can_create_project(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        # 가드가 과차단하지 않는지 - worker는 정상 생성.
        resp = await test_client.post(
            "/api/v1/projects", headers=_auth(worker_token), json=_project_payload()
        )
        assert resp.status_code == 201, resp.text
