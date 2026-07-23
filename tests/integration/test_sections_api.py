"""섹션 조회·편집 API 통합 테스트 — 트리/본문/수동편집/AI재작성.

sections 테이블에 직접 행을 심어 조회·편집 경로를 검증한다(파이프라인 관통은 별도).
AI 재작성은 라우터의 _section_rewriter 시드를 fake로 교체해 실검색·실LLM 없이 검증한다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routers import projects as projects_router
from src.core.types import SectionDraft, SectionPlan
from src.db.models.project import Project
from src.db.models.section import Section


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_project(session: AsyncSession, owner_id: UUID) -> Project:
    project = Project(
        title="섹션 테스트",
        topic="고령화 대응",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="completed",
    )
    session.add(project)
    await session.flush()
    return project


async def _seed_sections(session: AsyncSession, project_id: UUID) -> list[Section]:
    rows = [
        Section(
            id=uuid4(),
            project_id=project_id,
            chapter_number=1,
            section_number=1,
            chapter_title="개요",
            title="배경",
            level=2,
            content="배경 본문 [1]",
            source_ids=[uuid4()],
            qa_status="passed",
            status="completed",
        ),
        Section(
            id=uuid4(),
            project_id=project_id,
            chapter_number=1,
            section_number=2,
            chapter_title="개요",
            title="목적",
            level=2,
            content="목적 본문",
            source_ids=[],
            qa_status="passed",
            status="completed",
        ),
        Section(
            id=uuid4(),
            project_id=project_id,
            chapter_number=2,
            section_number=1,
            chapter_title="분석",
            title="현황",
            level=2,
            content="",
            source_ids=[],
            qa_status="pending",
            status="pending",
        ),
    ]
    session.add_all(rows)
    await session.commit()
    return rows


class TestSectionsApi:
    async def test_tree_groups_by_chapter(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        await _seed_sections(test_session, project.id)

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sections", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 200, resp.text
        tree = resp.json()["tree"]
        assert [c["title"] for c in tree] == ["개요", "분석"]
        assert tree[0]["level"] == 1
        assert [s["title"] for s in tree[0]["children"]] == ["배경", "목적"]
        # 1장은 전부 completed → completed, 2장은 pending
        assert tree[0]["status"] == "completed"
        assert tree[1]["status"] == "pending"
        # 절 노드 parent_id는 장 노드 id
        assert tree[0]["children"][0]["parent_id"] == tree[0]["id"]

    async def test_get_and_patch_content(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        rows = await _seed_sections(test_session, project.id)
        sid = rows[0].id

        got = await test_client.get(
            f"/api/v1/projects/{project.id}/sections/{sid}", headers=_auth(super_admin_token)
        )
        assert got.status_code == 200
        assert got.json()["content"] == "배경 본문 [1]"
        assert len(got.json()["source_ids"]) == 1

        patched = await test_client.patch(
            f"/api/v1/projects/{project.id}/sections/{sid}",
            headers=_auth(super_admin_token),
            json={"content": "사람이 고친 본문"},
        )
        assert patched.status_code == 200
        assert patched.json()["content"] == "사람이 고친 본문"

    async def test_get_missing_section_404(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sections/{uuid4()}",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 404

    async def test_rewrite_saves_generated_content(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        rows = await _seed_sections(test_session, project.id)
        sid = rows[2].id  # pending 섹션

        cited = uuid4()

        async def _fake_rewriter(proj, plan: SectionPlan, instruction: str) -> SectionDraft:
            assert plan.section_id == sid
            assert instruction == "더 간결하게"
            return SectionDraft(
                section_id=sid, content="AI 재작성 결과 [1]", cited_chunk_ids=[cited]
            )

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake_rewriter)

        resp = await test_client.post(
            f"/api/v1/projects/{project.id}/sections/{sid}/rewrite",
            headers=_auth(super_admin_token),
            json={"instruction": "더 간결하게"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content"] == "AI 재작성 결과 [1]"
        assert body["source_ids"] == [str(cited)]
        assert body["qa_status"] == "passed"

    async def test_sections_cascade_on_project_delete(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        await _seed_sections(test_session, project.id)

        deleted = await test_client.delete(
            f"/api/v1/projects/{project.id}", headers=_auth(super_admin_token)
        )
        assert deleted.status_code == 204

        gone = await test_client.get(
            f"/api/v1/projects/{project.id}/sections", headers=_auth(super_admin_token)
        )
        # 프로젝트가 사라졌으니 404
        assert gone.status_code == 404
