"""프로젝트 목록 상태 필터 — '진행 중'(in_progress) 그룹 + '완료' 단일 + 오값 422.

'진행 중'은 단일 단계가 아니라 완료·보관·취소가 아닌 모든 진행 단계(created~reviewing)를
묶는다. 방금 생성한(status=created) 프로젝트도 여기 잡혀야 한다(옛 탭 설계의 빈 탭 문제 해소).
"""

from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_project(test_client: AsyncClient, token: str, title: str) -> dict:
    resp = await test_client.post(
        "/api/v1/projects",
        headers=_auth(token),
        json={
            "title": title,
            "topic": "상태 필터 테스트",
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
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestProjectListStatusFilter:
    async def test_in_progress_groups_nonterminal_stages(
        self,
        test_client: AsyncClient,
        worker_token: str,
        test_session: AsyncSession,
    ) -> None:
        # 방금 생성 → status=created (진행 중 그룹). 다른 하나는 완료 처리.
        in_prog = await _create_project(test_client, worker_token, "진행 중 프로젝트")
        done = await _create_project(test_client, worker_token, "완료 프로젝트")
        project = await test_session.get(Project, UUID(done["id"]))
        assert project is not None
        project.status = "completed"
        await test_session.commit()

        # status=in_progress → created 프로젝트는 잡히고, 완료는 제외.
        resp = await test_client.get(
            "/api/v1/projects", headers=_auth(worker_token), params={"status": "in_progress"}
        )
        assert resp.status_code == 200, resp.text
        ids = {p["id"] for p in resp.json()}
        assert in_prog["id"] in ids
        assert done["id"] not in ids

        # status=completed → 완료만.
        resp2 = await test_client.get(
            "/api/v1/projects", headers=_auth(worker_token), params={"status": "completed"}
        )
        assert resp2.status_code == 200, resp2.text
        ids2 = {p["id"] for p in resp2.json()}
        assert done["id"] in ids2
        assert in_prog["id"] not in ids2

    async def test_invalid_status_returns_422(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.get(
            "/api/v1/projects", headers=_auth(worker_token), params={"status": "bogus"}
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_STATUS_FILTER"
