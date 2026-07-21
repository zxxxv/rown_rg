"""웹 소스 인덱싱 — 파일 없는 웹 콘텐츠(content_md)를 chunks에 넣는 경로.

VectorIndexingService(vector.py)는 파일 파싱 전용이라 web_search 소스를 못 넣는다.
여기서는 파싱 단계만 건너뛰고 동일한 뒷단(chunk_markdown → embed → chunks INSERT)을
재사용한다. ProjectSource DB는 이미 source_type='web_search'를 허용한다(마이그 제약).

트랜잭션 모델은 vector.py와 동일: source 행 INSERT(세션 #1) → 임베딩(세션 밖, 느림)
→ chunks 일괄 INSERT(세션 #2). 임베딩이 DB 트랜잭션을 오래 잡지 않게 한다.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.project_source import ProjectSource
from src.services.indexing.vector import IndexingResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.clients.embedding_client import EmbeddingClient
    from src.services.indexing._chunking import ChunkingService

logger = structlog.get_logger(__name__)

Track = str  # "content" | "style"
Reliability = str  # "high" | "medium" | "low"


class WebSourceIndexer:
    """웹 콘텐츠(마크다운) 한 건을 chunks에 인덱싱. 파일 파서를 거치지 않는다."""

    def __init__(
        self,
        *,
        chunking_service: ChunkingService,
        embedding_client: EmbeddingClient,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._chunking = chunking_service
        self._embedding = embedding_client
        self._session_maker = session_maker

    async def index(
        self,
        *,
        project_id: UUID,
        content_md: str,
        url: str | None = None,
        title: str | None = None,
        track: Track = "content",
        reliability: Reliability | None = None,
    ) -> IndexingResult:
        """웹 소스 1건을 청킹·임베딩해 chunks에 INSERT.

        source_id는 클라이언트에서 생성(uuid4)해 round-trip 없이 chunks에 연결한다.
        web_search 소스는 부분 UNIQUE 대상이 아니라(두 키 NULL) 매번 새 행이 된다.
        """
        t0 = time.perf_counter()
        source_id = uuid4()

        # 세션 #1: project_sources 행 INSERT. 임베딩 전에 commit하고 닫는다.
        async with self._session_maker() as session:
            session.add(
                ProjectSource(
                    id=source_id,
                    project_id=project_id,
                    source_type="web_search",
                    title=title,
                    url=url,
                    reliability=reliability,
                    metadata_={},
                )
            )
            await session.commit()

        chunks = await self._chunking.chunk_markdown(content_md, source_id)
        if not chunks:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info(
                "web_indexing.complete",
                project_id=str(project_id),
                source_id=str(source_id),
                chunks_created=0,
                elapsed_ms=round(elapsed, 1),
            )
            return IndexingResult(
                source_id=source_id, chunks_created=0, parse_cached=False, elapsed_ms=elapsed
            )

        embed_results = await self._embedding.embed_batch([c.content for c in chunks])

        # 세션 #2: chunks 일괄 INSERT.
        async with self._session_maker() as session:
            session.add_all(
                [
                    ChunkModel(
                        project_id=project_id,
                        source_id=source_id,
                        track=track,
                        content=c.content,
                        embedding=embed_results[i].embedding,
                        chunk_index=c.chunk_index,
                        metadata_=c.metadata,
                    )
                    for i, c in enumerate(chunks)
                ]
            )
            await session.commit()

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            "web_indexing.complete",
            project_id=str(project_id),
            source_id=str(source_id),
            chunks_created=len(chunks),
            elapsed_ms=round(elapsed, 1),
        )
        return IndexingResult(
            source_id=source_id,
            chunks_created=len(chunks),
            parse_cached=False,
            elapsed_ms=elapsed,
        )


def build_web_source_indexer() -> WebSourceIndexer:
    """공유 임베딩 모델 + ChunkingService로 WebSourceIndexer를 조립.

    임베딩 모델은 get_embedding_client() 싱글턴을 쓰므로 검색·인덱싱이 한 모델을 공유한다.
    최초 호출 시 BGE-M3가 로드된다(무거움).
    """
    from src.clients.embedding_factory import get_embedding_client
    from src.db.session import async_session_maker
    from src.services.indexing._chunking import ChunkingService

    embedder = get_embedding_client()
    chunking = ChunkingService(embedder)
    return WebSourceIndexer(
        chunking_service=chunking,
        embedding_client=embedder,
        session_maker=async_session_maker,
    )
