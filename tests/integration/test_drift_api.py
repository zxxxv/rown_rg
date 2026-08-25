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

    async def test_rewrite_clears_the_unreflected_mark(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch,
    ):
        """재작성이 지문을 다시 찍어 루프를 닫는다.

        안 찍으면 미반영 절을 다시 써도 배지가 그대로 남아, 사용자가 무엇을 더 해야
        하는지 알 수 없다(0047과 한 세트).
        """
        from src.api.routers import projects as projects_router
        from src.core.types import SectionDraft

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        patch = await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )
        assert patch.status_code == 200, patch.text
        before = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert before.json()["n_plan_changed"] == 1

        async def _fake_rewriter(_proj, plan, _instruction) -> SectionDraft:
            # 재작성은 **새 계획**을 읽는다 - 지문도 그 계획으로 찍혀야 한다.
            assert plan.direction == "완전히 다른 방향"
            return SectionDraft(
                section_id=sid, content="새 방향으로 다시 쓴 본문", cited_chunk_ids=[]
            )

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake_rewriter)

        resp = await test_client.post(
            f"/api/v1/projects/{pid}/sections/{sid}/rewrite",
            headers=_auth(worker_token),
            json={"instruction": ""},
        )
        assert resp.status_code == 200, resp.text

        after = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert after.json()["sections"] == [], "재작성했으면 미반영이 사라져야 한다"

    async def test_batch_rewrite_goes_through_the_same_apply_path(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """묶음 재작성도 절 1개 경로와 같은 반영 로직을 쓴다.

        따로 짰다가 전역 인용 재매핑·근거 추적 meta·버전 스냅샷을 통째로 빠뜨렸었다
        (2026-08-26). 두 경로가 갈라지면 묶음으로 고친 절만 문서와 어긋난다.
        """
        from src.api.routers.projects import _apply_section_rewrite, _plan_for_row
        from src.core.types import SectionDraft
        from src.db.models.project import Project as ProjectModel

        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        await test_client.patch(
            f"/api/v1/projects/{pid}/config",
            headers=_auth(worker_token),
            json={"config": {"outline": _outline("완전히 다른 방향", sid)}},
        )

        pool = [uuid.uuid4(), uuid.uuid4()]
        project = await test_session.get(ProjectModel, pid)
        row = await test_session.get(Section, sid)
        await test_session.refresh(row)
        draft = SectionDraft(
            section_id=sid,
            content="묶음으로 다시 쓴 본문",
            cited_chunk_ids=[],
            pool_chunk_ids=pool,
        )

        # 묶음 경로가 부르는 바로 그 함수. 배경 태스크 배선은 test_jobs가 덮는다.
        await _apply_section_rewrite(
            test_session,
            project,
            row,
            _plan_for_row(project, row),
            draft,
            created_by=worker_user.id,
        )
        await test_session.commit()

        await test_session.refresh(row)
        assert row.content == "묶음으로 다시 쓴 본문"
        # 근거 추적 meta가 채워져야 한다 - 절 1개 경로와 같은 계약.
        assert row.meta["pool_chunk_ids"] == [str(c) for c in pool]
        assert row.meta["evidence_count"] == 2

        # 버전이 얼려져야 한다 - 되돌릴 지점.
        versions = await test_client.get(
            f"/api/v1/projects/{pid}/versions", headers=_auth(worker_token)
        )
        assert any(v["reason"].startswith("rewrite:") for v in versions.json())

        # 그리고 미반영이 사라진다.
        after = await test_client.get(f"/api/v1/projects/{pid}/drift", headers=_auth(worker_token))
        assert after.json()["sections"] == []

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
