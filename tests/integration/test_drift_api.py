"""미반영 현황 API — 완료 보고서의 목차를 고쳐도 본문이 살아 있고, 어긋남이 드러난다.

2026-08-25 설계 전환: 보고서는 완성 순간이 끝이 아니다. 완료 상태에서도 목차를 고칠 수
있고(동결 해제), 고친 내용이 본문에 닿기 전까지 그 절은 "미반영"으로 표시된다.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.types import SectionPlan
from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User
from src.services.sections.drift import content_fingerprint


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _outline(direction: str, sid: uuid.UUID) -> dict:
    return {
        "chapters": [
            {
                "id": "ch1",
                "title": "1장",
                "sections": [
                    {
                        "id": str(sid),
                        "title": "배경",
                        "direction": direction,
                        "key_points": [],
                        "agents": [],
                    }
                ],
            }
        ]
    }


async def _completed_project(
    session: AsyncSession, owner_id: uuid.UUID, sid: uuid.UUID, direction: str
) -> uuid.UUID:
    plan = SectionPlan(
        section_id=sid,
        chapter_number=1,
        section_number=1,
        title="배경",
        chapter_title="1장",
        direction=direction,
    )
    proj = Project(
        title="미반영 테스트",
        topic="주제",
        config={
            "outline": _outline(direction, sid),
            "_section_plan": [plan.model_dump(mode="json")],
        },
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
            chapter_number=1,
            section_number=1,
            chapter_title="1장",
            title="배경",
            content="이미 쓰인 본문입니다.",
            source_ids=[],
            plan_hash=content_fingerprint(plan),
            status="completed",
        )
    )
    await session.commit()
    return proj.id


class TestDriftApi:
    async def test_unchanged_report_has_no_drift(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        pid = await _completed_project(test_session, worker_user.id, uuid.uuid4(), "원래 방향")
        resp = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["sections"] == []

    async def test_outline_edit_on_completed_report_marks_section_unreflected(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """완료 상태에서도 목차를 고칠 수 있고, 그 절이 미반영으로 드러난다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")

        patch = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )
        assert patch.status_code == 200, patch.text

        resp = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        body = resp.json()
        assert body["n_plan_changed"] == 1
        assert body["sections"][0]["reasons"] == ["plan_changed"]
        assert body["sections"][0]["label"] == "1.1 배경"

        # 본문은 그대로 살아 있어야 한다 — 미반영은 '표시'지 '삭제'가 아니다.
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        assert row.content == "이미 쓰인 본문입니다."

    async def test_archived_report_stays_frozen(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        proj = await test_session.get(Project, pid)
        proj.status = "archived"
        await test_session.commit()

        resp = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("딴 방향", sid)}},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PROJECT_CONFIG_FROZEN"
