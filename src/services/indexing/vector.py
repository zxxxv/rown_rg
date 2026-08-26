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

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.core.config import settings
from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.project_source import ProjectSource
from src.services.indexing._boilerplate import excluded_metadata
from src.services.indexing._pages import assign_chunk_pages, strip_page_markers
from src.services.indexing.published_year import extract_published_year
from src.services.qa.span_vectors import store_quietly

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
    # 자료 발간연도(본문 머리·파일명 추출) — 자료 검토 게이트·통계의 연도 배지가
    # 자료 단위 값을 원한다(청크 태깅만으론 목록 표시가 안 된다, 2026-08-17).
    published_year: int | None = None
    # 어느 파서가 본문을 만들었나 + 파싱 경고. project_sources 메타로 영속돼
    # "pymupdf로 떨어져 표가 평문이 된" 자료를 화면이 구분할 수 있게 한다 -
    # 전에는 경고가 로그에만 남아 폴백이 화면에서 정상으로 보였다(2026-08-20 실사고).
    parser_name: str = ""
    parse_warnings: list[str] = Field(default_factory=list)


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

    # (문서 제목 추출 헬퍼들은 모듈 하단 _extract_doc_title 참조)

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

        doc_title = _extract_doc_title(parse_result.markdown)
        # 세션 #1: source UPSERT + 기존 chunks 청소. 임베딩 호출 전에 commit하고 닫는다.
        async with self._session_maker() as session:
            source_id = await self._upsert_source(session, source)
            if doc_title:
                await self._apply_doc_title(session, source_id, doc_title)
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
        # 발간연도 — 자료 시점 축의 재료(2026-08-15). 청킹 전에 뽑아 0청크 경로에도
        # 결과로 싣는다(자료 검토 게이트가 자료 단위 값을 쓴다).
        year = extract_published_year(source.title or Path(source.file_path).name, clean_md[:4000])
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
                published_year=year,
                parser_name=parse_result.parser_name,
                parse_warnings=parse_result.warnings,
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

        # 청크에도 연도를 실어 작성 주입(근거 라벨)이 읽게 한다. 확신 없으면 안 단다.
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
            models = [
                ChunkModel(
                    # id를 여기서 정한다 - 대목 벡터가 청크 id로 매이는데, DB가 만들게
                    # 두면 id를 알려고 flush를 한 번 더 해야 한다. 값은 서버 기본값과
                    # 같은 uuid4라 저장되는 것에 차이가 없다.
                    id=uuid4(),
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
            session.add_all(models)
            # 배제된 청크는 검색에 안 나오므로 근거가 될 일이 없다 - 대목 벡터도 안 만든다.
            span_targets = [
                (m.id, m.content)
                for m, meta in zip(models, metas, strict=True)
                if not meta.get("excluded")
            ]
            await session.commit()

        # 대목 벡터 - 근거 대조가 볼 때마다 다시 만들던 것을 여기서 한 번 만든다.
        # 자료 한 건당 ~2.6초. 실패해도 색인은 성공이다(services/qa/span_vectors).
        await store_quietly(self._session_maker, span_targets, client=self._embedding_client)

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
            published_year=year,
            parser_name=parse_result.parser_name,
            parse_warnings=parse_result.warnings,
        )

    async def _apply_doc_title(self, session, source_id: UUID, doc_title: str) -> None:
        """파싱된 본문에서 뽑은 문서 제목을 자료 행에 반영한다(2026-08-21 사용자 요청).

        참고문헌·화면에 "63_16609_file_pdf_1646878149.pdf" 같은 ID형 파일명이 그대로
        노출되던 것의 처방 — 표시 제목은 **파일명이 ID형일 때만** 교체하고(멀쩡한
        한글 파일명은 존중), 추출 제목은 항상 metadata.doc_title로 보존해 이후 UI
        제안에 쓴다.
        """
        row = await session.get(ProjectSource, source_id)
        if row is None:
            return
        meta = dict(row.metadata_ or {})
        if meta.get("doc_title") != doc_title:
            meta["doc_title"] = doc_title
            row.metadata_ = meta  # JSONB는 재할당해야 dirty로 잡힌다
        if _looks_like_id_filename(row.title or ""):
            logger.info(
                "indexing.title_from_document",
                source_id=str(source_id),
                old=row.title,
                new=doc_title,
            )
            row.title = doc_title

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


# --- 문서 제목 추출(2026-08-21) — 파싱된 본문 앞머리에서 실제 표제를 뽑는다 ---

# ID형 파일명 판정 — 한글이 없고, 줄기가 숫자·16진·구분기호 위주면 표제가 아니다.
_HANGUL_RE = re.compile(r"[가-힣]")
_IDISH_STEM_RE = re.compile(r"^[0-9A-Za-z_\-.\s\[\]+%()]+$")


def _looks_like_id_filename(title: str) -> bool:
    if not title or _HANGUL_RE.search(title):
        return False  # 한글 파일명은 사람이 지은 제목으로 존중한다
    stem = re.sub(r"\.(pdf|docx|hwpx|txt|md)$", "", title.strip(), flags=re.I)
    if not _IDISH_STEM_RE.fullmatch(stem):
        return False
    digits = sum(c.isdigit() for c in stem)
    return digits >= 4 and digits / max(1, len(stem)) >= 0.3


# 제목 후보로 못 쓰는 줄 — 페이지 마커·그림 생략 표기·표 행·구분선.
_TITLE_REJECT_RE = re.compile(r"intentionally omitted|^\||^[-=_*\s]+$|^\d+$|^page \d+", re.I)


def _clean_title_line(line: str) -> str:
    text = re.sub(r"^#{1,4}\s*", "", line.strip())
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s*\d+\)\s*$", "", text)  # 표제 끝 각주 마커("… 시사점 1)") 제거
    return " ".join(text.split())


def _extract_doc_title(markdown: str) -> str | None:
    """본문 앞 40줄에서 표제 한 줄 — 첫 헤딩 우선, 없으면 첫 실속 있는 줄.

    docling은 표지·첫 장의 표제를 대개 '#' 헤딩이나 첫 줄로 내놓는다. 확신이 없으면
    None — 잘못된 제목은 ID형 파일명보다 나쁘다(교체는 ID형일 때만 하므로 이중 안전).
    """
    if len(markdown) < 200:
        return None  # 표제를 논할 크기가 아니다(테스트 더미·빈 파일 방어)
    lines = markdown.split("\n")[:40]
    fallback: str | None = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or _TITLE_REJECT_RE.search(line):
            continue
        cleaned = _clean_title_line(line)
        if not (4 <= len(cleaned) <= 90):
            continue
        tokens = cleaned.split()
        if len(tokens) >= 3 and all(len(t) == 1 for t in tokens):
            # "산 업 경 제 분 석" — 자간을 벌린 잡지 러브릭·장식 표제. 표제가 아니다
            # (2026-08-21 실측: KIET 표지에서 오인).
            continue
        if line.startswith("#"):
            # 표제가 두 줄로 접힌 경우 — 연결어로 끝나면 다음 실속 줄을 이어붙인다
            # (2026-08-21 실측: "EU 탄소국경조정제도의" 에서 잘림).
            if cleaned[-1] in "의및과와를은는·,":
                for nxt in lines[i + 1 : i + 4]:
                    nxt_clean = _clean_title_line(nxt)
                    if nxt_clean and not _TITLE_REJECT_RE.search(nxt.strip()):
                        joined = f"{cleaned} {nxt_clean}"
                        if len(joined) <= 90:
                            cleaned = joined
                        break
            return cleaned
        if fallback is None:
            fallback = cleaned
    return fallback


async def extract_and_apply_doc_title(source_id: UUID, file_path: Path) -> None:
    """업로드 직후 표제만 선추출해 자료 행에 반영한다(색인 유예와 독립).

    색인이 런의 색인 단계로 유예되면(1dd66ad) 자료 검토 게이트가 ID형 파일명을
    그대로 보게 된다(2026-08-21 지적) — 파싱과 표제 추출만 가볍게 먼저 한다.
    파싱 결과는 파스 캐시에 남아 런 색인이 재사용하므로 이중 비용이 없다.
    """
    from src.clients.parser import ParserRegistry
    from src.db.session import async_session_maker

    parsed = await ParserRegistry().parse(file_path)
    title = _extract_doc_title(parsed.markdown)
    if not title:
        return
    async with async_session_maker() as session:
        row = await session.get(ProjectSource, source_id)
        if row is None:
            return
        meta = dict(row.metadata_ or {})
        if meta.get("doc_title") != title:
            meta["doc_title"] = title
            row.metadata_ = meta  # JSONB는 재할당해야 dirty로 잡힌다
        if _looks_like_id_filename(row.title or ""):
            logger.info("upload.title_from_document", source_id=str(source_id), new=title)
            row.title = title
        await session.commit()
