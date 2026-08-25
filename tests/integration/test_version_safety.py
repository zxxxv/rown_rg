"""안전망 — 저장하면 버전이 남고, 절 단위로 되돌릴 수 있다.

종전에는 **AI가 고치면 버전이 남고 사람이 고치면 안 남았다**(재작성·블록 재작성만
스냅샷). 되돌리고 싶은 순간은 오히려 손으로 고친 쪽이 많다. 그리고 되돌리는 경로가
아예 없어 버전은 쌓이기만 했다(2026-08-26).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project(session: AsyncSession, owner_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    sid = uuid.uuid4()
    proj = Project(
        title="안전망",
        topic="주제",
        config={},
        status="completed",
        depth_mode="full_report",
        owner_id=owner_id,
    )
    session.add(proj)
    await session.flush()
    session.add(
        Section(
            id=sid,
            project_id=proj.id,
            chapter_number=3,
            section_number=2,
            chapter_title="3장",
            title="비용편익",
            content="원래 본문입니다.",
            source_ids=[],
            status="completed",
        )
    )
    await session.commit()
    return proj.id, sid


async def _versions(client: AsyncClient, pid: uuid.UUID, token: str) -> list[dict]:
    r = await client.get(f"/api/v1/projects/{pid}/versions", headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()


class TestSaveMakesAVersion:
    async def test_manual_edit_is_versioned(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid, sid = await _project(test_session, worker_user.id)
        r = await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "손으로 고친 본문입니다."},
        )
        assert r.status_code == 200, r.text
        vs = await _versions(test_client, pid, worker_token)
        assert [v["reason"] for v in vs] == ["edit:3.2"]

    async def test_identical_save_makes_no_new_version(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """무해한 저장은 조용하다 — 내용 지문이 같으면 버전을 안 만든다."""
        pid, sid = await _project(test_session, worker_user.id)
        for _ in range(2):
            await test_client.patch(
                f"/api/v1/projects/{pid}/sections/{sid}",
                headers=_auth(worker_token),
                json={"content": "같은 본문"},
            )
        assert len(await _versions(test_client, pid, worker_token)) == 1

    async def test_outline_edit_is_versioned(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """목차 수정은 절 행을 재정렬하고 미반영을 만든다 — 되돌릴 지점이 있어야 한다."""
        pid, sid = await _project(test_session, worker_user.id)
        r = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={
                "config": {
                    "outline": {
                        "chapters": [
                            {
                                "title": "3장",
                                "sections": [
                                    {
                                        "id": str(sid),
                                        "title": "비용편익",
                                        "direction": "새 방향",
                                        "key_points": [],
                                        "agents": [],
                                    }
                                ],
                            }
                        ]
                    }
                }
            },
        )
        assert r.status_code == 200, r.text
        assert "outline" in [v["reason"] for v in await _versions(test_client, pid, worker_token)]


class TestRestoreOneSection:
    async def test_restore_brings_back_the_old_text(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid, sid = await _project(test_session, worker_user.id)
        # v1 = "원래 본문" 시점을 얼린다(직접 저장으로).
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "1차 수정본입니다."},
        )
        vs = await _versions(test_client, pid, worker_token)
        v1 = min(v["version_no"] for v in vs)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "2차 수정본입니다."},
        )

        r = await test_client.post(
            f"/api/v1/projects/{pid}/versions/{v1}/restore/{sid}", headers=_auth(worker_token)
        )
        assert r.status_code == 200, r.text
        assert r.json()["section"] == "3.2"

        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "1차 수정본입니다."
        # 되돌리기도 덮어쓰기라 흔적이 남는다 — 되돌린 것을 다시 되돌릴 수 있어야 한다.
        assert any(
            v["reason"] == "restore:3.2" for v in await _versions(test_client, pid, worker_token)
        )

    async def test_missing_section_in_version_is_404(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid, sid = await _project(test_session, worker_user.id)
        await test_client.patch(
            f"/api/v1/projects/{pid}/sections/{sid}",
            headers=_auth(worker_token),
            json={"content": "수정본"},
        )
        vs = await _versions(test_client, pid, worker_token)
        r = await test_client.post(
            f"/api/v1/projects/{pid}/versions/{vs[0]['version_no']}/restore/{uuid.uuid4()}",
            headers=_auth(worker_token),
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "VERSION_SECTION_NOT_FOUND"
