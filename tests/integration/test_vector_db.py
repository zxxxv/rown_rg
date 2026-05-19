"""Integration tests for VectorIndexingService against a real Postgres DB.

Covers: UPSERT semantics on the partial UNIQUE indexes (library / upload),
DELETE-then-INSERT idempotency on re-indexing, and that chunks land with
the correct ``source_id`` linkage. Parser and embedding client are still
stubbed — the focus here is the DB transactional path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.clients.parser.base import ParseMetadata, ParseResult
from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from src.infrastructure.auth.password_handler import hash_password
from src.services.indexing._chunking import Chunk
from src.services.indexing.vector import SourceInput, VectorIndexingService

pytestmark = pytest.mark.asyncio


async def _seed_project(session: AsyncSession) -> Project:
    user = User(
        email=f"vec-{uuid4().hex[:6]}@test.com",
        name="vec",
        role="worker",
        password_hash=hash_password("Vector123!@"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    project = Project(title="vec-project", topic="topic", owner_id=user.id)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def _seed_library_node(session: AsyncSession) -> LibraryNode:
    node = LibraryNode(name="lib.hwpx", type="file", file_path="/lib/lib.hwpx")
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


def _make_service(
    session_maker: async_sessionmaker[AsyncSession],
    chunks: list[Chunk],
    *,
    markdown: str = "# T\n\nbody",
) -> tuple[VectorIndexingService, MagicMock]:
    parser = MagicMock()
    parser.parse = AsyncMock(
        return_value=ParseResult(
            source_path=Path("/tmp/x.hwpx"),
            markdown=markdown,
            metadata=ParseMetadata(char_count=len(markdown)),
        )
    )

    chunking = MagicMock()
    chunking.chunk_markdown = AsyncMock(return_value=chunks)

    embed = MagicMock()
    embed_results = [_embed(c.content) for c in chunks]
    embed.embed_batch = AsyncMock(return_value=embed_results)

    svc = VectorIndexingService(
        parser_registry=parser,
        chunking_service=chunking,
        embedding_client=embed,
        session_maker=session_maker,
    )
    return svc, embed


def _embed(text: str) -> Any:
    r = MagicMock()
    r.embedding = [0.001] * 1024
    r.text = text
    r.cached = False
    return r


def _chunks(source_id, n: int) -> list[Chunk]:
    return [
        Chunk(
            content=f"content-{i}",
            char_count=9,
            chunk_index=i,
            source_id=source_id,
            metadata={"chunk_type": "text", "header_path": []},
        )
        for i in range(n)
    ]


class TestUpsertRouting:
    async def test_library_source_upsert_creates_row(
        self, test_session: AsyncSession, test_session_maker
    ) -> None:
        project = await _seed_project(test_session)
        node = await _seed_library_node(test_session)

        # source_id는 upsert가 반환할 값이지만, _chunks는 그 전에 만들어야 해서 dummy로 둔다.
        # ChunkingService를 mock하므로 실제 chunk.source_id 값은 INSERT 시 어차피 service가
        # upsert 결과로 덮어쓰지 않는다 — Chunk 모델 컬럼은 service가 직접 채우니 무해.
        svc, _ = _make_service(test_session_maker, _chunks(uuid4(), 2))

        result = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
                title="Title-A",
            )
        )

        async with test_session_maker() as s:
            rows = (
                (
                    await s.execute(
                        select(ProjectSource).where(ProjectSource.project_id == project.id)
                    )
                )
                .scalars()
                .all()
            )

        assert len(rows) == 1
        assert rows[0].id == result.source_id
        assert rows[0].library_node_id == node.id
        assert rows[0].upload_path is None
        assert rows[0].title == "Title-A"

    async def test_library_source_upsert_updates_on_conflict(
        self, test_session: AsyncSession, test_session_maker
    ) -> None:
        project = await _seed_project(test_session)
        node = await _seed_library_node(test_session)
        svc, _ = _make_service(test_session_maker, _chunks(uuid4(), 1))

        first = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
                title="Old",
            )
        )
        second = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
                title="New",
            )
        )

        assert first.source_id == second.source_id  # UPSERT — 같은 row.
        async with test_session_maker() as s:
            row = (
                await s.execute(select(ProjectSource).where(ProjectSource.id == first.source_id))
            ).scalar_one()
        assert row.title == "New"  # DO UPDATE 적용.

    async def test_upload_source_independent_from_library(
        self, test_session: AsyncSession, test_session_maker
    ) -> None:
        # 같은 project에 library와 upload 소스가 공존 가능해야 함 (부분 UNIQUE 두 개 분리).
        project = await _seed_project(test_session)
        node = await _seed_library_node(test_session)
        svc, _ = _make_service(test_session_maker, _chunks(uuid4(), 1))

        r1 = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
            )
        )
        r2 = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
                upload_path="/uploads/y.hwpx",
            )
        )

        assert r1.source_id != r2.source_id
        async with test_session_maker() as s:
            count = (
                (
                    await s.execute(
                        select(ProjectSource).where(ProjectSource.project_id == project.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(count) == 2


class TestReindexIdempotency:
    async def test_reindex_replaces_existing_chunks(
        self, test_session: AsyncSession, test_session_maker
    ) -> None:
        project = await _seed_project(test_session)
        node = await _seed_library_node(test_session)

        svc_first, _ = _make_service(test_session_maker, _chunks(uuid4(), 3))
        first = await svc_first.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
            )
        )

        async with test_session_maker() as s:
            initial_count = (
                (await s.execute(select(ChunkModel).where(ChunkModel.source_id == first.source_id)))
                .scalars()
                .all()
            )
        assert len(initial_count) == 3

        # 재인덱싱 — chunks 2개로 줄어든 응답을 가정.
        svc_second, _ = _make_service(test_session_maker, _chunks(uuid4(), 2))
        second = await svc_second.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
            )
        )

        assert second.source_id == first.source_id
        async with test_session_maker() as s:
            rows = (
                (
                    await s.execute(
                        select(ChunkModel)
                        .where(ChunkModel.source_id == second.source_id)
                        .order_by(ChunkModel.chunk_index)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert [r.chunk_index for r in rows] == [0, 1]


class TestTrackPersistence:
    async def test_style_track_propagates_to_chunks(
        self, test_session: AsyncSession, test_session_maker
    ) -> None:
        project = await _seed_project(test_session)
        node = await _seed_library_node(test_session)
        svc, _ = _make_service(test_session_maker, _chunks(uuid4(), 2))

        result = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
                track="style",
            )
        )

        async with test_session_maker() as s:
            rows = (
                (
                    await s.execute(
                        select(ChunkModel).where(ChunkModel.source_id == result.source_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 2
        assert {r.track for r in rows} == {"style"}


class TestEmptyChunksFastPath:
    async def test_no_chunks_inserts_nothing(
        self, test_session: AsyncSession, test_session_maker
    ) -> None:
        project = await _seed_project(test_session)
        node = await _seed_library_node(test_session)

        svc, embed = _make_service(test_session_maker, chunks=[], markdown="")
        result = await svc.index_source(
            SourceInput(
                project_id=project.id,
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=node.id,
            )
        )

        assert result.chunks_created == 0
        embed.embed_batch.assert_not_awaited()
        async with test_session_maker() as s:
            rows = (
                (
                    await s.execute(
                        select(ChunkModel).where(ChunkModel.source_id == result.source_id)
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []
