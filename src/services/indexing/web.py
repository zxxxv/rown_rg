"""웹 소스 스테이징·인덱싱 — 파일 없는 웹 콘텐츠(content_md)를 다루는 경로.

확정(SOURCE_POOL) 게이트를 사이에 두고 두 단계로 나뉜다:
- stage(): 수집된 웹 출처를 project_sources 행으로 저장한다. 원문(content_md)은
  metadata_(JSONB)에 담아 게이트 너머(재개=별도 프로세스, DB에서 복원)까지 살려둔다.
  임베딩은 하지 않는다.
- index_existing(): 사람이 채택한(is_included=true) 출처만 청킹·임베딩해 chunks에 넣는다.

이렇게 나눠 임베딩을 확정 이후로 미룬다 — 제외된 자료는 임베딩 자체를 건너뛰어 비용을 아낀다.
VectorIndexingService(vector.py)는 파일 파싱 전용이라 web_search 소스를 못 넣는다. 여기서는
파싱 단계만 건너뛰고 동일한 뒷단(chunk_markdown → embed → chunks INSERT)을 재사용한다.

트랜잭션 모델은 vector.py와 동일: source 행 INSERT(세션 #1, stage) → 임베딩(세션 밖, 느림)
→ chunks 일괄 INSERT(세션 #2, index_existing). 임베딩이 DB 트랜잭션을 오래 잡지 않게 한다.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel
from sqlalchemy import select

from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.project_source import ProjectSource
from src.services.indexing._boilerplate import excluded_metadata
from src.services.indexing.exclusion import AUTO_EXCLUDED_KEY
from src.services.indexing.published_year import extract_published_year, year_from_page_age
from src.services.indexing.vector import IndexingResult
from src.services.qa.span_vectors import store_quietly

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.clients.embedding_client import EmbeddingClient
    from src.services.indexing._chunking import ChunkingService

logger = structlog.get_logger(__name__)

Track = str  # "content" | "style"
Reliability = str  # "high" | "medium" | "low"

# 원문을 게이트 너머까지 살려두는 저장 위치(project_sources.metadata_의 키).
_CONTENT_MD_KEY = "content_md"


class StagedWebSource(BaseModel):
    """확정 후 색인 대상으로 DB에서 읽어온 웹 출처 1건(원문 포함)."""

    source_id: UUID
    content_md: str
    title: str | None = None
    url: str | None = None


class WebSourceIndexer:
    """웹 콘텐츠(마크다운)를 확정 전(stage)·후(index_existing)로 나눠 처리. 파서를 안 거친다."""

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

    async def stage(
        self,
        *,
        project_id: UUID,
        content_md: str,
        url: str | None = None,
        title: str | None = None,
        reliability: Reliability | None = None,
        matched_sections: list[str] | None = None,
        page_age: str | None = None,
    ) -> UUID:
        """웹 출처 1건을 project_sources에 저장하고 source_id를 반환(임베딩 안 함).

        원문은 metadata_[content_md]에 담겨 확정 게이트 이후 index_existing이 읽어간다.
        source_id는 클라이언트에서 생성(uuid4)해 게이트 payload의 SourceRef.id와 일치시킨다 —
        사람이 제외한 id가 그대로 is_included=false로 반영·색인 제외되게 한다.

        업서트: 같은 URL의 기존 행이 본문 없이 남아 있으면(회수 실패 잔재) 새 행을
        만들지 않고 그 행을 채운다 — '추가 검색'이 실패 출처를 재회수하면 껍데기가
        실자료로 승격되고, id·채택 상태가 유지된다.
        """
        async with self._session_maker() as session:
            if url:
                existing = (
                    (
                        await session.execute(
                            select(ProjectSource).where(
                                ProjectSource.project_id == project_id,
                                ProjectSource.url == url,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is not None:
                    meta = dict(existing.metadata_ or {})
                    if not (meta.get(_CONTENT_MD_KEY) or "").strip():
                        meta[_CONTENT_MD_KEY] = content_md or ""
                        meta["matched_sections"] = list(
                            matched_sections or meta.get("matched_sections") or []
                        )
                        meta["page_age"] = page_age or meta.get("page_age")
                        # 0청크라 자동 제외됐던 껍데기가 실자료로 승격되면 채택도 복구한다
                        # (사람이 제외한 행에는 auto_excluded가 없어 그대로 남는다).
                        if (content_md or "").strip() and meta.pop(AUTO_EXCLUDED_KEY, None):
                            meta.pop("index_error", None)
                            existing.is_included = True
                        existing.metadata_ = meta
                        existing.title = title or existing.title
                        existing.reliability = reliability or existing.reliability
                        await session.commit()
                    # 본문이 이미 있으면 갱신 없이 기존 id 반환(중복 행 방지)
                    return existing.id
            source_id = uuid4()
            session.add(
                ProjectSource(
                    id=source_id,
                    project_id=project_id,
                    source_type="web_search",
                    title=title,
                    url=url,
                    reliability=reliability,
                    metadata_={
                        _CONTENT_MD_KEY: content_md or "",
                        "matched_sections": list(matched_sections or []),
                        "page_age": page_age,
                    },
                )
            )
            await session.commit()
        return source_id

    async def load_included(self, project_id: UUID) -> list[StagedWebSource]:
        """확정 후 남은(is_included=true) 웹 출처를 원문과 함께 읽는다(색인 대상)."""
        async with self._session_maker() as session:
            rows = (
                (
                    await session.execute(
                        select(ProjectSource).where(
                            ProjectSource.project_id == project_id,
                            ProjectSource.source_type == "web_search",
                            ProjectSource.is_included.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [
            StagedWebSource(
                source_id=row.id,
                content_md=(row.metadata_ or {}).get(_CONTENT_MD_KEY) or "",
                title=row.title,
                url=row.url,
            )
            for row in rows
        ]

    async def index_existing(
        self,
        *,
        project_id: UUID,
        source_id: UUID,
        content_md: str,
        track: Track = "content",
    ) -> IndexingResult:
        """이미 존재하는 source_id의 원문을 청킹·임베딩해 chunks에 INSERT.

        project_sources 행은 stage()가 이미 만들었으므로 여기서는 chunks만 넣는다.
        """
        t0 = time.perf_counter()
        chunks = await self._chunking.chunk_markdown(content_md, source_id) if content_md else []
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

        async with self._session_maker() as session:
            metas = await excluded_metadata(session, project_id, chunks)
            # 발간연도 — 수집이 준 page_age가 1순위, 없으면 제목·본문 머리에서 추출
            # (업로드 색인과 같은 축, 2026-08-15). 웹 자료의 시점 미상 현재형 서술을
            # 작성 주입·게이트 표시가 걸러낼 재료다.
            src_row = await session.get(ProjectSource, source_id)
            year = (
                year_from_page_age((src_row.metadata_ or {}).get("page_age")) if src_row else None
            )
            if year is None:
                year = extract_published_year(src_row.title if src_row else None, content_md[:4000])
            if year is not None:
                for meta in metas:
                    meta["published_year"] = year
            models = [
                ChunkModel(
                    # id를 여기서 정한다 - 대목 벡터가 청크 id로 매이는데, DB가 만들게
                    # 두면 id를 알려고 flush를 한 번 더 해야 한다. 값은 서버 기본값과
                    # 같은 uuid4라 저장되는 것에 차이가 없다.
                    id=uuid4(),
                    project_id=project_id,
                    source_id=source_id,
                    track=track,
                    content=c.content,
                    embedding=embed_results[i].embedding,
                    chunk_index=c.chunk_index,
                    metadata_=metas[i],
                )
                for i, c in enumerate(chunks)
            ]
            session.add_all(models)
            # 배제된 청크는 검색에 안 나오므로 근거가 될 일이 없다 - 대목 벡터도 안 만든다.
            span_targets = [
                (m.id, m.content)
                for m, meta in zip(models, metas, strict=True)
                if not meta.get("excluded")
            ]
            await session.commit()

        # 대목 벡터 - 근거 대조가 볼 때마다 다시 만들던 것을 여기서 한 번 만든다.
        # 실패해도 색인은 성공이다(services/qa/span_vectors).
        await store_quietly(self._session_maker, span_targets, client=self._embedding_client)

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
            published_year=year,
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
