"""Unit tests for VectorIndexingService — dependencies fully mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.clients.parser.base import ParseMetadata, ParseResult
from src.services.indexing._chunking import Chunk
from src.services.indexing.vector import (
    IndexingResult,
    SourceInput,
    VectorIndexingService,
)


def _parse_result(markdown: str = "# Title\n\nbody.", cached: bool = False) -> ParseResult:
    return ParseResult(
        source_path=Path("/tmp/dummy.hwpx"),
        markdown=markdown,
        metadata=ParseMetadata(char_count=len(markdown)),
        warnings=[],
        cached=cached,
    )


def _embed_result(text: str) -> MagicMock:
    m = MagicMock()
    m.embedding = [0.1] * 1024
    m.text = text
    m.cached = False
    return m


def _empty_hashes() -> MagicMock:
    """청크 INSERT 직전의 '기존 본문 해시 조회' 응답 - 중복 없음.

    색인기는 근거로 못 쓰는 청크(보일러플레이트·내용 중복)를 metadata.excluded로
    표시하려고 세션 #2에서 해시를 한 번 읽는다(_boilerplate.excluded_metadata).
    """
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    return result


def _fake_session_maker(session_mock: MagicMock) -> MagicMock:
    """Return an async_sessionmaker stub that yields the same session per call."""
    maker = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session_mock)
    cm.__aexit__ = AsyncMock(return_value=None)
    maker.return_value = cm
    return maker


def _session_with_source_id(source_id: UUID, deleted: int = 0) -> MagicMock:
    """Build a session mock whose execute() returns the given source UUID then a delete result."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.add_all = MagicMock()

    upsert_result = MagicMock()
    upsert_result.scalar_one = MagicMock(return_value=source_id)

    delete_result = MagicMock()
    delete_result.rowcount = deleted

    session.execute = AsyncMock(side_effect=[upsert_result, delete_result])
    return session


class TestSourceInputValidator:
    def test_library_requires_node_id(self) -> None:
        with pytest.raises(ValueError, match="library_node_id"):
            SourceInput(
                project_id=uuid4(),
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
            )

    def test_library_rejects_upload_path(self) -> None:
        with pytest.raises(ValueError, match="upload_path"):
            SourceInput(
                project_id=uuid4(),
                source_type="library",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=uuid4(),
                upload_path="/uploads/a.hwpx",
            )

    def test_upload_requires_path(self) -> None:
        with pytest.raises(ValueError, match="upload_path"):
            SourceInput(
                project_id=uuid4(),
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
            )

    def test_upload_rejects_library_node_id(self) -> None:
        with pytest.raises(ValueError, match="library_node_id"):
            SourceInput(
                project_id=uuid4(),
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
                library_node_id=uuid4(),
                upload_path="/uploads/a.hwpx",
            )

    def test_library_valid(self) -> None:
        inp = SourceInput(
            project_id=uuid4(),
            source_type="library",
            file_path=Path("/tmp/x.hwpx"),
            library_node_id=uuid4(),
        )
        assert inp.upload_path is None

    def test_upload_valid(self) -> None:
        inp = SourceInput(
            project_id=uuid4(),
            source_type="upload",
            file_path=Path("/tmp/x.hwpx"),
            upload_path="/uploads/a.hwpx",
        )
        assert inp.library_node_id is None


class TestPipelineOrdering:
    @pytest.mark.asyncio
    async def test_stages_called_in_order(self) -> None:
        project_id = uuid4()
        source_id = uuid4()

        parser = MagicMock()
        parser.parse = AsyncMock(return_value=_parse_result(cached=True))

        chunks = [
            Chunk(
                content="hello world",
                char_count=11,
                chunk_index=0,
                source_id=source_id,
                metadata={"chunk_type": "text", "header_path": []},
            )
        ]
        chunking = MagicMock()
        chunking.chunk_markdown = AsyncMock(return_value=chunks)

        embed = MagicMock()
        embed.embed_batch = AsyncMock(return_value=[_embed_result("hello world")])

        session1 = _session_with_source_id(source_id, deleted=0)
        session2 = MagicMock()
        session2.commit = AsyncMock()
        session2.add_all = MagicMock()
        session2.execute = AsyncMock(return_value=_empty_hashes())

        maker_call_count = {"n": 0}

        def _make_cm() -> MagicMock:
            maker_call_count["n"] += 1
            sess = session1 if maker_call_count["n"] == 1 else session2
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=sess)
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

        session_maker = MagicMock(side_effect=_make_cm)

        svc = VectorIndexingService(
            parser_registry=parser,
            chunking_service=chunking,
            embedding_client=embed,
            session_maker=session_maker,
        )

        result = await svc.index_source(
            SourceInput(
                project_id=project_id,
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
                upload_path="/uploads/a.hwpx",
            )
        )

        # 세션 4개 사용: upsert+delete 1개, insert 1개, 대목 벡터(store_quietly) 1개,
        # 용어 채굴(terms.mine_and_store_quietly) 1개 — 모의 세션이라 읽기에서 조기
        # 종료되지만(비치명 계약) 세션은 연다.
        assert maker_call_count["n"] == 4
        # 임베딩은 두 세션 사이에 청크 콘텐츠 순서대로 호출된다. 두 번째 호출은
        # 대목 벡터(store_quietly)의 것 - 첫 호출의 인자만 계약이다.
        assert embed.embed_batch.await_args_list[0].args == (["hello world"],)
        # 청킹은 upsert가 반환한 source_id를 받는다.
        chunking.chunk_markdown.assert_awaited_once()
        assert chunking.chunk_markdown.call_args.args[1] == source_id
        # 최종 결과는 상태를 반영한다.
        assert isinstance(result, IndexingResult)
        assert result.source_id == source_id
        assert result.chunks_created == 1
        assert result.parse_cached is True

    @pytest.mark.asyncio
    async def test_empty_chunks_short_circuits_embedding(self) -> None:
        source_id = uuid4()

        parser = MagicMock()
        parser.parse = AsyncMock(return_value=_parse_result(markdown=""))

        chunking = MagicMock()
        chunking.chunk_markdown = AsyncMock(return_value=[])

        embed = MagicMock()
        embed.embed_batch = AsyncMock()

        session_maker = _fake_session_maker(_session_with_source_id(source_id))

        svc = VectorIndexingService(
            parser_registry=parser,
            chunking_service=chunking,
            embedding_client=embed,
            session_maker=session_maker,
        )

        result = await svc.index_source(
            SourceInput(
                project_id=uuid4(),
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
                upload_path="/uploads/empty.hwpx",
            )
        )

        embed.embed_batch.assert_not_awaited()
        assert result.chunks_created == 0
        assert result.source_id == source_id


class TestParserErrorPropagation:
    @pytest.mark.asyncio
    async def test_parser_error_propagates(self) -> None:
        parser = MagicMock()
        parser.parse = AsyncMock(side_effect=RuntimeError("parse boom"))

        svc = VectorIndexingService(
            parser_registry=parser,
            chunking_service=MagicMock(),
            embedding_client=MagicMock(),
            session_maker=MagicMock(),
        )

        with pytest.raises(RuntimeError, match="parse boom"):
            await svc.index_source(
                SourceInput(
                    project_id=uuid4(),
                    source_type="upload",
                    file_path=Path("/tmp/x.hwpx"),
                    upload_path="/uploads/a.hwpx",
                )
            )


class TestEmbeddingOrderAlignment:
    @pytest.mark.asyncio
    async def test_embedding_results_aligned_with_chunk_order(self) -> None:
        source_id = uuid4()

        chunks = [
            Chunk(
                content=f"text-{i}",
                char_count=6,
                chunk_index=i,
                source_id=source_id,
                metadata={"chunk_type": "text", "header_path": []},
            )
            for i in range(3)
        ]

        parser = MagicMock()
        parser.parse = AsyncMock(return_value=_parse_result())
        chunking = MagicMock()
        chunking.chunk_markdown = AsyncMock(return_value=chunks)
        embed = MagicMock()
        embed.embed_batch = AsyncMock(return_value=[_embed_result(f"text-{i}") for i in range(3)])

        added_rows: list[list] = []

        def _capture(rows: list) -> None:
            added_rows.append(rows)

        session1 = _session_with_source_id(source_id)
        session2 = MagicMock()
        session2.commit = AsyncMock()
        session2.add_all = MagicMock(side_effect=_capture)
        session2.execute = AsyncMock(return_value=_empty_hashes())

        calls = iter([session1, session2])

        def _make_cm() -> MagicMock:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=next(calls))
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

        svc = VectorIndexingService(
            parser_registry=parser,
            chunking_service=chunking,
            embedding_client=embed,
            session_maker=MagicMock(side_effect=_make_cm),
        )

        await svc.index_source(
            SourceInput(
                project_id=uuid4(),
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
                upload_path="/uploads/a.hwpx",
            )
        )

        embed.embed_batch.assert_awaited_once_with([f"text-{i}" for i in range(3)])
        # add_all은 청크 순서 그대로 행을 받는다.
        assert len(added_rows) == 1
        rows = added_rows[0]
        assert [r.chunk_index for r in rows] == [0, 1, 2]
        assert [r.content for r in rows] == [f"text-{i}" for i in range(3)]


class TestDeleteBeforeInsertOrdering:
    @pytest.mark.asyncio
    async def test_delete_runs_in_first_session_before_embedding(self) -> None:
        source_id = uuid4()
        call_log: list[str] = []

        parser = MagicMock()
        parser.parse = AsyncMock(return_value=_parse_result())

        chunks = [
            Chunk(
                content="x",
                char_count=1,
                chunk_index=0,
                source_id=source_id,
                metadata={"chunk_type": "text", "header_path": []},
            )
        ]
        chunking = MagicMock()
        chunking.chunk_markdown = AsyncMock(return_value=chunks)

        embed = MagicMock()

        async def _embed(_texts: list[str]) -> list:
            call_log.append("embed")
            return [_embed_result("x")]

        embed.embed_batch = _embed

        upsert_result = MagicMock()
        upsert_result.scalar_one = MagicMock(return_value=source_id)
        delete_result = MagicMock()
        delete_result.rowcount = 3

        async def _exec(_stmt):
            # 첫 호출 = UPSERT, 두 번째 = DELETE
            call_log.append("execute")
            return upsert_result if call_log.count("execute") == 1 else delete_result

        session1 = MagicMock()
        session1.execute = _exec
        session1.commit = AsyncMock(side_effect=lambda: call_log.append("commit1"))

        session2 = MagicMock()
        session2.execute = AsyncMock(return_value=_empty_hashes())
        session2.add_all = MagicMock(side_effect=lambda _rows: call_log.append("add_all"))
        session2.commit = AsyncMock(side_effect=lambda: call_log.append("commit2"))

        calls = iter([session1, session2])

        def _make_cm() -> MagicMock:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=next(calls))
            cm.__aexit__ = AsyncMock(return_value=None)
            return cm

        svc = VectorIndexingService(
            parser_registry=parser,
            chunking_service=chunking,
            embedding_client=embed,
            session_maker=MagicMock(side_effect=_make_cm),
        )

        await svc.index_source(
            SourceInput(
                project_id=uuid4(),
                source_type="upload",
                file_path=Path("/tmp/x.hwpx"),
                upload_path="/uploads/a.hwpx",
            )
        )

        # 임베딩은 첫 commit 이후에만 호출돼야 함 (DB 락 유지 금지).
        embed_pos = call_log.index("embed")
        commit1_pos = call_log.index("commit1")
        assert commit1_pos < embed_pos
        # 두 번째 commit은 add_all 후.
        assert call_log.index("add_all") < call_log.index("commit2")
