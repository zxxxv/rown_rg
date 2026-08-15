"""Vector RAG indexing — single source end-to-end.

Pipeline: parse → upsert ``project_sources`` → chunk → embed → insert
``chunks``. The orchestrator owns ordering and DB transactions only; each
stage is delegated to its specialist (parser registry, chunking service,
embedding client).

Transactional model: short, sequential sessions. One session for the
source UPSERT/DELETE-existing-chunks block, then the session closes
before BGE-M3 runs — embedding is the slow stage and must not hold a DB
transaction open. A second session reopens to bulk-insert the new chunks.

Sibling files in this directory will own RAG variants that *consume*
``chunks`` (e.g. ``raptor.py``, ``graph.py``). They are not extensions of
this file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.config import settings
from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.project_source import ProjectSource
from src.services.indexing._boilerplate import excluded_metadata
from src.services.indexing._pages import assign_chunk_pages, strip_page_markers
from src.services.indexing.published_year import extract_published_year

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.clients.embedding_client import EmbeddingClient
    from src.clients.parser import ParserRegistry
    from src.services.indexing._chunking import ChunkingService

logger = structlog.get_logger(__name__)


SourceType = Literal["library", "upload"]
Track = Literal["content", "style"]


class SourceInput(BaseModel):
    """One source to ingest into the vector index.

    Exactly one of ``library_node_id`` / ``upload_path`` must be set,
    matching ``source_type``. ``web_search`` sources are not handled here
    — they have no file to parse and need their own ingestion path.
    """

    project_id: UUID
    source_type: SourceType
    file_path: Path
    library_node_id: UUID | None = None
    upload_path: str | None = None
    track: Track = "content"
    title: str | None = None
    url: str | None = None
    reliability: Literal["high", "medium", "low"] | None = None

    @model_validator(mode="after")
    def _check_key(self) -> SourceInput:
        if self.source_type == "library":
            if self.library_node_id is None or self.upload_path is not None:
                raise ValueError(
                    "source_type='library' requires library_node_id (and no upload_path)"
                )
        elif self.source_type == "upload":
            if self.upload_path is None or self.library_node_id is not None:
                raise ValueError(
                    "source_type='upload' requires upload_path (and no library_node_id)"
                )
        return self


class IndexingResult(BaseModel):
    """Outcome of a single source indexing run."""

    source_id: UUID
    chunks_created: int
    parse_cached: bool
    elapsed_ms: float
    # 파서가 읽은 페이지 수(PDF 등). 라이브러리 목록의 '페이지' 열이 이 값을 쓴다 —
    # 색인 때 말고는 알 수 없어 여기서 실어 보낸다(2026-08-10: 계속 '-'로 비어 있었다).
    page_count: int | None = None


class VectorIndexingService:
    """End-to-end vector RAG indexer for one source at a time.

    The constructor only collects dependencies; all I/O happens in
    :meth:`index_source`. Safe to instantiate once and reuse across many
    sources because each call opens its own short-lived DB sessions and
    builds a fresh per-call ``SemanticChunker`` inside ``ChunkingService``.
    """

    def __init__(
        self,
        *,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        embedding_client: EmbeddingClient,
        session_maker: async_sessionmaker,
    ) -> None:
        self._parser_registry = parser_registry
        self._chunking_service = chunking_service
        self._embedding_client = embedding_client
        self._session_maker = session_maker

    async def index_source(self, source: SourceInput) -> IndexingResult:
        """Run the full pipeline for one source.

        Steps: parse → UPSERT source row → DELETE existing chunks for that
        source → chunk markdown → embed chunks → bulk INSERT chunks.

        Args:
            source: What to index. The file at ``source.file_path`` is
                read by the parser registry; library/upload keys decide
                the UPSERT path on ``project_sources``.

        Returns:
            :class:`IndexingResult` with the source UUID (existing or new),
            number of chunks created, parse cache hit flag, and elapsed
            wall-clock time.
        """
        t0 = time.perf_counter()
        logger.info(
            "indexing.start",
            project_id=str(source.project_id),
            source_type=source.source_type,
            file_path=str(source.file_path),
        )

        parse_result = await self._parser_registry.parse(source.file_path)
        logger.info(
            "indexing.parsed",
            project_id=str(source.project_id),
            parse_cached=parse_result.cached,
            markdown_length=len(parse_result.markdown),
        )

        # 세션 #1: source UPSERT + 기존 chunks 청소. 임베딩 호출 전에 commit하고 닫는다.
        async with self._session_maker() as session:
            source_id = await self._upsert_source(session, source)
            deleted = await self._delete_existing_chunks(session, source_id)
            await session.commit()
        logger.info(
            "indexing.upserted",
            project_id=str(source.project_id),
            source_id=str(source_id),
            deleted_chunks=deleted,
        )

        # PDF 파서가 심은 페이지 경계 마커를 걷어내고 청킹한다 - 마커가 청크·임베딩·
        # 프롬프트에 새면 안 된다. 페이지 번호는 청크 metadata로만 남긴다.
        clean_md, page_starts = strip_page_markers(parse_result.markdown)
        chunks = await self._chunking_service.chunk_markdown(clean_md, source_id)
        if not chunks:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info(
                "indexing.complete",
                source_id=str(source_id),
                chunks_created=0,
                elapsed_ms=round(elapsed, 1),
            )
            return IndexingResult(
                source_id=source_id,
                chunks_created=0,
                parse_cached=parse_result.cached,
                elapsed_ms=elapsed,
                page_count=parse_result.metadata.page_count,
            )

        # 청크별 시작 페이지 - "PDF 원본 p.N 열기" 점프의 재료. 페이지를 모르는
        # 문서(웹·HWPX 등)는 아무것도 달지 않는다.
        for chunk, page in zip(
            chunks,
            assign_chunk_pages([c.content for c in chunks], clean_md, page_starts),
            strict=True,
        ):
            if page is not None:
                chunk.metadata["page"] = page

        # 발간연도 — 자료 시점 축의 재료(2026-08-15). 청크에 실어 작성 주입·게이트
        # 표시·통계가 전부 여기서 읽게 한다. 확신 없으면 안 단다(미상은 미상대로 처리).
        year = extract_published_year(source.title or Path(source.file_path).name, clean_md[:4000])
        if year is not None:
            for chunk in chunks:
                chunk.metadata["published_year"] = year

        # 본문 임베딩은 외부 I/O — DB 세션 밖에서. BGE-M3 자체 캐시가 재실행 시 비용을 흡수.
        # 배치를 끊어 넣는다: 한 번에 넣으면 배치 안 최장 청크에 맞춰 전부 패딩돼
        # 중간 텐서가 자료 크기에 비례해 부푼다(실측 14GB). 벡터 자체는 청크당 4KB라
        # 전부 들고 있어도 무해하고, 터지는 건 추론 중간값이다.
        size = max(1, settings.embedding_batch_size)
        embed_results = []
        for start in range(0, len(chunks), size):
            batch = chunks[start : start + size]
            embed_results.extend(
                await self._embedding_client.embed_batch([c.content for c in batch])
            )

        # 세션 #2: 새 chunks 일괄 INSERT. 근거로 못 쓰는 청크는 여기서 표시한다 —
        # 지우지 않고 검색에서만 뺀다(원문 대조 화면은 모델이 받은 것을 그대로 보여줘야 한다).
        async with self._session_maker() as session:
            metas = await excluded_metadata(session, source.project_id, chunks)
            session.add_all(
                [
                    ChunkModel(
                        project_id=source.project_id,
                        source_id=source_id,
                        track=source.track,
                        content=c.content,
                        embedding=embed_results[i].embedding,
                        chunk_index=c.chunk_index,
                        metadata_=metas[i],
                    )
                    for i, c in enumerate(chunks)
                ]
            )
            await session.commit()

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            "indexing.complete",
            source_id=str(source_id),
            chunks_created=len(chunks),
            elapsed_ms=round(elapsed, 1),
        )
        return IndexingResult(
            page_count=parse_result.metadata.page_count,
            source_id=source_id,
            chunks_created=len(chunks),
            parse_cached=parse_result.cached,
            elapsed_ms=elapsed,
        )

    async def _upsert_source(self, session, source: SourceInput) -> UUID:
        """Upsert into project_sources via the appropriate partial UNIQUE index.

        Returns the resulting source UUID (existing on conflict, or new).
        The two source types route to different partial indexes so a
        library row never conflicts with an upload row sharing a project.
        """
        values = {
            "project_id": source.project_id,
            "library_node_id": source.library_node_id,
            "upload_path": source.upload_path,
            "source_type": source.source_type,
            "title": source.title,
            "url": source.url,
            "reliability": source.reliability,
        }
        stmt = pg_insert(ProjectSource).values(**values)

        if source.source_type == "library":
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "library_node_id"],
                index_where=ProjectSource.library_node_id.isnot(None),
                set_={
                    "title": stmt.excluded.title,
                    "url": stmt.excluded.url,
                    "reliability": stmt.excluded.reliability,
                },
            )
        else:
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "upload_path"],
                index_where=ProjectSource.upload_path.isnot(None),
                set_={
                    "title": stmt.excluded.title,
                    "url": stmt.excluded.url,
                    "reliability": stmt.excluded.reliability,
                },
            )
        stmt = stmt.returning(ProjectSource.id)
        result = await session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    async def _delete_existing_chunks(session, source_id: UUID) -> int:
        """Delete chunks belonging to a source. Returns the row count removed."""
        # 재인덱싱 멱등성 — 새 INSERT 전에 기존 청크를 비워 같은 source_id에 중복이 쌓이지
        # 않게 한다. raptor/graph 노드는 chunks.source_id를 직접 참조하지 않으므로 영향 없음.
        result = await session.execute(delete(ChunkModel).where(ChunkModel.source_id == source_id))
        return result.rowcount or 0

    async def source_exists(self, project_id: UUID, source_id: UUID) -> bool:
        """True if the given source row exists under the project."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(ProjectSource.id).where(
                    ProjectSource.id == source_id,
                    ProjectSource.project_id == project_id,
                )
            )
            return result.first() is not None


def build_vector_indexing_service() -> VectorIndexingService:
    """파일 소스(upload·library) 색인기를 공유 임베딩 모델로 조립.

    검색·인덱싱이 같은 BGE-M3 싱글턴(get_embedding_client)을 공유한다. 파서 레지스트리는
    기본값(HWPX·PDF)을 쓴다. 최초 호출 시 임베딩 모델이 로드된다(무거움) — 요청 처리 중
    호출하므로 lazy import로 모듈 로드를 가볍게 유지한다.
    """
    from src.clients.embedding_factory import get_embedding_client
    from src.clients.parser import ParserRegistry
    from src.db.session import async_session_maker
    from src.services.indexing._chunking import ChunkingService

    embedder = get_embedding_client()
    return VectorIndexingService(
        parser_registry=ParserRegistry(),
        chunking_service=ChunkingService(embedder),
        embedding_client=embedder,
        session_maker=async_session_maker,
    )
