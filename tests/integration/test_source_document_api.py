"""자료 원문 문서 API — 근거 패널의 원문 뷰어가 읽는 경로.

파일을 재파싱하지 않고 색인 청크를 원문 순서(chunk_index)로 이어 붙여 내려준다.
근거 추적의 span 오프셋이 청크 기준이라 청크 경계가 보존되는지가 계약의 핵심이다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from tests.conftest import auth_headers as _auth


async def _make_project(session: AsyncSession, owner_id: UUID) -> Project:
    project = Project(
        title="원문 뷰어 테스트",
        topic="시장 동향",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="completed",
    )
    session.add(project)
    await session.flush()
    return project


async def _seed_source(session: AsyncSession, project_id: UUID) -> ProjectSource:
    src = ProjectSource(
        project_id=project_id,
        source_type="web_search",
        title="시장 보고서",
        url="https://example.com/report",
        reliability="high",
    )
    session.add(src)
    await session.flush()
    # 심는 순서를 일부러 뒤섞는다 - 응답은 chunk_index 순이어야 한다.
    session.add_all(
        [
            Chunk(
                project_id=project_id,
                source_id=src.id,
                track="content",
                content=f"본문 조각 {i}",
                chunk_index=i,
                metadata_={"header_path": ["1장 개요"] if i < 2 else ["2장 분석"]},
            )
            for i in (2, 0, 1)
        ]
    )
    # style 트랙은 문체 참고용이지 이 자료의 본문이 아니다 - 섞여 나오면 안 된다.
    session.add(
        Chunk(
            project_id=project_id,
            source_id=src.id,
            track="style",
            content="문체 청크",
            chunk_index=99,
            metadata_={},
        )
    )
    await session.commit()
    return src


class TestSourceDocumentApi:
    async def test_returns_chunks_in_document_order(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        src = await _seed_source(test_session, project.id)

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sources/{src.id}/document",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source_id"] == str(src.id)
        assert body["title"] == "시장 보고서"
        assert body["url"] == "https://example.com/report"
        assert body["source_type"] == "web_search"
        assert [c["chunk_index"] for c in body["chunks"]] == [0, 1, 2]
        assert [c["content"] for c in body["chunks"]] == [
            "본문 조각 0",
            "본문 조각 1",
            "본문 조각 2",
        ]
        assert body["chunks"][0]["header_path"] == ["1장 개요"]
        assert body["chunks"][2]["header_path"] == ["2장 분석"]

    async def test_source_of_other_project_is_hidden(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        mine = await _make_project(test_session, super_admin_user.id)
        other = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        src = await _seed_source(test_session, other.id)

        # 자료는 존재하지만 요청한 프로젝트 소속이 아니다 - 존재를 숨기고 404.
        resp = await test_client.get(
            f"/api/v1/projects/{mine.id}/sources/{src.id}/document",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 404

    async def test_unknown_source_is_404(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sources/{uuid4()}/document",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 404

    async def test_page_metadata_passthrough(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        # PDF 색인이 남긴 metadata.page가 문서 뷰로 그대로 내려와야 "p.N 열기"가 선다.
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        src = ProjectSource(
            project_id=project.id, source_type="upload", upload_path="/tmp/x.pdf", title="x.pdf"
        )
        test_session.add(src)
        await test_session.flush()
        test_session.add(
            Chunk(
                project_id=project.id,
                source_id=src.id,
                track="content",
                content="3쪽에서 온 본문",
                chunk_index=0,
                metadata_={"page": 3},
            )
        )
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sources/{src.id}/document",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["chunks"][0]["page"] == 3


class TestSourceFileApi:
    async def test_serves_upload_file_inline(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
        tmp_path,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake body")
        src = ProjectSource(
            project_id=project.id, source_type="upload", upload_path=str(pdf), title="doc.pdf"
        )
        test_session.add(src)
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sources/{src.id}/file",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"
        # 브라우저 뷰어로 열려야 #page=N이 동작한다 - 강제 다운로드 헤더가 있으면 안 된다.
        assert "attachment" not in resp.headers.get("content-disposition", "")
        assert resp.content == b"%PDF-1.4 fake body"

    async def test_web_source_has_no_file(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        await test_session.commit()
        src = await _seed_source(test_session, project.id)  # web_search - 파일 없음

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/sources/{src.id}/file",
            headers=_auth(super_admin_token),
        )
        assert resp.status_code == 404
