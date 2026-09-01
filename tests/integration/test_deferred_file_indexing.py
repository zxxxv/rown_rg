"""실행 전 첨부의 색인 유예를 런 색인 단계가 받는지 — 라이브러리 구멍의 회귀 고정.

2026-08-27 철강 런 실사고: 실행 전 라이브러리 첨부는 attach 경로가 색인을
"런의 색인 단계로" 미루는데(index_deferred), 그 단계(_index_pending_file_sources)가
업로드만 골라 유예를 아무도 안 받았다. 핵심 PDF가 채택된 채 0청크로 남아
보고서가 그 문서 없이 쓰였다.

계약:
  ① 청크 없는 라이브러리 소스도 색인 대상이다(파일 경로는 library_nodes에서 해소)
  ② 색인 후 index_deferred/indexing 메타가 걷힌다
  ③ 노드에 파일이 없으면(수집 원문만 있는 자료) 조용히 건너뛴다 — 실패 아님
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.workflows.stages import _index_pending_file_sources


@pytest.fixture(autouse=True)
def _wire_session_maker(monkeypatch, test_session_maker):
    monkeypatch.setattr("src.db.session.async_session_maker", test_session_maker)


class _StubResult:
    chunks_created = 3
    page_count = 2


class _StubService:
    def __init__(self) -> None:
        self.calls = []

    async def index_source(self, source):
        self.calls.append(source)
        return _StubResult()


@pytest.fixture
def stub_service(monkeypatch) -> _StubService:
    stub = _StubService()
    monkeypatch.setattr("src.services.indexing.vector.build_vector_indexing_service", lambda: stub)
    return stub


async def _project(session: AsyncSession, owner_id) -> Project:
    project = Project(
        title="유예 색인",
        topic="철강",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="indexing",
    )
    session.add(project)
    await session.flush()
    return project


class TestDeferredLibraryIndexing:
    async def test_deferred_library_source_is_indexed(
        self, super_admin_user, test_session: AsyncSession, stub_service, tmp_path
    ) -> None:
        project = await _project(test_session, super_admin_user.id)
        pdf = tmp_path / "발표자료.pdf"
        pdf.write_bytes(b"%PDF-1.4 stub")
        node = LibraryNode(name="발표자료.pdf", type="file", file_path=str(pdf))
        test_session.add(node)
        await test_session.flush()
        source = ProjectSource(
            project_id=project.id,
            source_type="library",
            library_node_id=node.id,
            title="발표자료.pdf",
            metadata_={"indexing": False, "index_deferred": True},
        )
        test_session.add(source)
        await test_session.commit()

        await _index_pending_file_sources(project.id)

        (call,) = stub_service.calls
        assert call.source_type == "library"
        assert call.library_node_id == node.id
        assert str(call.file_path) == str(pdf)
        await test_session.refresh(source)
        meta = source.metadata_ or {}
        # 유예 흔적이 걷히고 색인 결과가 새겨진다 - 프론트 폴링이 이 메타를 읽는다.
        assert "index_deferred" not in meta
        assert meta.get("chunks") == 3

    async def test_node_without_file_is_skipped(
        self, super_admin_user, test_session: AsyncSession, stub_service
    ) -> None:
        project = await _project(test_session, super_admin_user.id)
        node = LibraryNode(name="수집 원문만", type="file", file_path=None)
        test_session.add(node)
        await test_session.flush()
        source = ProjectSource(
            project_id=project.id,
            source_type="library",
            library_node_id=node.id,
            title="수집 원문만",
            metadata_={"index_deferred": True},
        )
        test_session.add(source)
        await test_session.commit()

        await _index_pending_file_sources(project.id)

        assert stub_service.calls == []
