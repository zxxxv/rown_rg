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
from tests.conftest import auth_headers as _auth


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
        # 자료 부족으로 썼던 절 - 재작성이 근거를 채우면 이 배지는 내려가야 한다
        # (재업로드 워크플로우의 마감 신호, 2026-08-13).
        rows[2].meta = {"volume_scaled": True}
        await test_session.commit()

        cited = uuid4()

        # 본문에 인용마커를 넣지 않는다: rewrite는 renumber_content로 인용을 프로젝트 출처의
        # 전역 번호로 재매핑하는데, 여기 cited는 실제 출처 청크가 아니라 매핑되지 않아
        # 마커가 정상적으로 제거된다. 이 테스트의 목적은 "재작성 내용·source_ids 저장"이므로
        # 매핑 의존 없는 본문으로 검증한다(인용 renumber 검증은 실제 출처가 있는 별도 테스트 몫).
        async def _fake_rewriter(proj, plan: SectionPlan, instruction: str) -> SectionDraft:
            assert plan.section_id == sid
            assert instruction == "더 간결하게"
            return SectionDraft(section_id=sid, content="AI 재작성 결과", cited_chunk_ids=[cited])

        monkeypatch.setattr(projects_router, "_section_rewriter", _fake_rewriter)

        resp = await test_client.post(
            f"/api/v1/projects/{project.id}/sections/{sid}/rewrite",
            headers=_auth(super_admin_token),
            json={"instruction": "더 간결하게"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["content"] == "AI 재작성 결과"
        assert body["source_ids"] == [str(cited)]
        assert body["qa_status"] == "passed"
        # 이번 재작성은 분량을 안 줄였으므로(volume_scaled=False) 자료 부족 배지가 내려간다
        assert body["evidence"]["scarce"] is False
        # 재작성은 이전 본문을 덮어쓰는 경로라 성공 직후가 버전으로 남는다(0045).
        versions = await test_client.get(
            f"/api/v1/projects/{project.id}/versions", headers=_auth(super_admin_token)
        )
        assert any(v["reason"].startswith("rewrite:") for v in versions.json())

    async def test_chart_block_is_not_rewritten_by_ai(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        """그래프 블록은 AI 재작성 대상이 아니다 — 모델이 펜스 안 수치를 고쳐 쓰면 근거와 끊긴다.

        표를 그래프로 바꾼 것은 사람이고, 그 수치는 이미 근거에 매여 게이트를 통과한 값이다.
        고칠 게 있으면 표로 되돌린 뒤 고치는 경로만 연다(LLM은 호출조차 하지 않는다).
        """
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        rows = await _seed_sections(test_session, project.id)
        row = rows[0]
        chart = "```chart\ntype: bar\nx: 미국 | 한국\nseries: 투자액 = 120 | 30\n```"
        row.content = f"배경 본문 [1]\n\n{chart}"
        await test_session.commit()

        resp = await test_client.post(
            f"/api/v1/projects/{project.id}/sections/{row.id}/rewrite-block",
            headers=_auth(super_admin_token),
            json={"block": chart, "instruction": "더 간결하게"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "BLOCK_IS_CHART"
        await test_session.refresh(row)
        assert chart in row.content  # 본문은 손대지 않는다

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


class TestDraftCitations:
    """조립 전(작성 중) 인용 라벨 — 수집 순서 직해석이 아니라 인용 청크의 자료로 푼다.

    실사례(2026-08-12): 이름이 비슷한 0청크 실패 업로드가 수집 순서상 앞자리를 차지해,
    작성 중 미리보기가 웹 출처 인용에 업로드 이름을 붙였다.
    """

    async def test_writing_labels_resolve_via_cited_chunks(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        from src.db.models.chunk import Chunk
        from src.db.models.project_source import ProjectSource

        project = await _make_project(test_session, super_admin_user.id)
        project.status = "writing"  # 조립 전 - 번호는 절-로컬
        # 수집 순서 1번: 0청크 실패 업로드. 옛 직해석([1]→수집 1번)은 이걸 라벨로 붙였다.
        dead = ProjectSource(
            project_id=project.id,
            source_type="upload",
            title="AI 반도체 동향(업로드)",
            upload_path="/tmp/dead.pdf",
        )
        web = ProjectSource(
            project_id=project.id,
            source_type="web_search",
            title="AI 반도체 동향(웹)",
            url="https://example.com/ai",
        )
        test_session.add_all([dead, web])
        await test_session.flush()
        chunk = Chunk(project_id=project.id, source_id=web.id, track="content", content="근거 본문")
        test_session.add(chunk)
        await test_session.flush()
        row = Section(
            id=uuid4(),
            project_id=project.id,
            chapter_number=1,
            section_number=1,
            chapter_title="개요",
            title="배경",
            level=2,
            content="주장 [1] 그리고 요약 근거 [2]",
            source_ids=[chunk.id],  # 첫 등장 번호 [1] ↔ source_ids[0] 규약
            qa_status="pending",
            status="writing",
        )
        test_session.add(row)
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sections/{row.id}", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 200, resp.text
        citations = resp.json()["citations"]
        assert [c["number"] for c in citations] == [1, 2]
        # [1]은 인용 청크가 속한 웹 출처 - 수집 순서 1번(죽은 업로드)이 아니다
        assert citations[0]["title"] == "AI 반도체 동향(웹)"
        assert citations[0]["source_id"] == str(web.id)
        # 매핑 없는 번호(요약 청크 등)는 확정 전 안내 문구
        assert citations[1]["title"] == "(조립 후 번호가 확정됩니다)"
        assert citations[1]["source_id"] is None
