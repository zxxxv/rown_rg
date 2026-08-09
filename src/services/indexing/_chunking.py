"""Markdown → semantic chunks for RAG indexing.

Tables are pre-extracted to ``<<<TABLE_N>>>`` placeholders so the
embedding-based splitter cannot slice mid-table; placeholder segments are
restored as their own ``chunk_type="table"`` chunks. Header chain is
captured by :class:`MarkdownHeaderTextSplitter` with ``strip_headers=False``
so the heading line stays in the body for LLM context.

The async :class:`EmbeddingClient` is bridged to LangChain's sync
``Embeddings`` via :class:`_LangChainEmbeddingsAdapter` — keeping the
project ABC stable lets us add new backends without inheriting LangChain
types.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar
from uuid import UUID

import structlog
from langchain_core.embeddings import Embeddings as LangChainEmbeddings
from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from pydantic import BaseModel, Field

from src.clients.embedding_client import EmbeddingClient
from src.core.config import settings

logger = structlog.get_logger(__name__)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")
_NUMBER_RE = re.compile(r"\d")
# 영문 약어 (GDP, MOU, ABC). 한국어 고유명사는 명사 사전 없이는 어려워 일단 보류 —
# 정확도가 필요한 검색은 BM25에 맡긴다.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z]{2,5}\b")
# ^로 라인 시작을 고정 — 인라인 ``|...|`` 표현이 잘못 매칭되는 것을 막는다. 코드블록 내부
# |는 별도 처리 없이도 보고서 본문에서 거의 등장하지 않아 무시 가능.
_TABLE_PATTERN = re.compile(
    r"(^\|[^\n]*\|\s*\n^\|[\s:|\-]+\|\s*\n(?:^\|[^\n]*\|\s*\n?)+)",
    re.MULTILINE,
)
_PLACEHOLDER_TEMPLATE = "<<<TABLE_{}>>>"
_PLACEHOLDER_RE = re.compile(r"<<<TABLE_(\d+)>>>")


class TablePlaceholderSplitError(RuntimeError):
    """Surface mid-table corruption — silently dropping a table would survive into the index."""


class Chunk(BaseModel):
    """One chunk ready for embedding/indexing.

    ``metadata`` keys: ``header_path``, ``chunk_type`` (``"text"`` |
    ``"table"``), ``token_count_estimate``, ``has_numbers``,
    ``has_proper_nouns``.
    """

    content: str
    char_count: int
    chunk_index: int
    source_id: UUID
    metadata: dict[str, Any] = Field(default_factory=dict)


class _LangChainEmbeddingsAdapter(LangChainEmbeddings):
    """Bridge our async :class:`EmbeddingClient` to LangChain's sync interface.

    Reason for the adapter rather than reshaping :class:`EmbeddingClient`
    around LangChain: the project ABC must remain stable so new backends
    (Qwen3, Gemma stubs) can be added without retrofitting third-party
    inheritance.

    Implementation: :class:`SemanticChunker.split_text` is synchronous and
    we call it from a worker thread (``asyncio.to_thread``). Inside that
    worker the adapter cannot use :func:`asyncio.run` (the caller's loop
    is still running), so we schedule each embedding coroutine back on
    the caller's loop via :func:`asyncio.run_coroutine_threadsafe` and
    block the worker on the future.
    """

    def __init__(self, client: EmbeddingClient, loop: asyncio.AbstractEventLoop) -> None:
        self._client = client
        self._loop = loop

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        fut = asyncio.run_coroutine_threadsafe(self._client.embed_batch(texts), self._loop)
        return [r.embedding for r in fut.result()]

    def embed_query(self, text: str) -> list[float]:
        fut = asyncio.run_coroutine_threadsafe(self._client.embed(text), self._loop)
        return fut.result().embedding


def _has_numbers(text: str) -> bool:
    return bool(_NUMBER_RE.search(text))


def _has_proper_nouns(text: str) -> bool:
    return bool(_PROPER_NOUN_RE.search(text))


def _build_header_path(metadata: dict[str, str]) -> list[str]:
    return [metadata[k] for k in ("h1", "h2", "h3") if k in metadata]


def _extract_tables(text: str) -> tuple[str, list[str]]:
    """Replace each table block with a numbered placeholder."""
    tables: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        idx = len(tables)
        tables.append(match.group(0).rstrip("\n"))
        return _PLACEHOLDER_TEMPLATE.format(idx)

    masked = _TABLE_PATTERN.sub(_replace, text)
    return masked, tables


def _split_by_placeholder(text: str) -> list[tuple[str, bool]]:
    """Return ``(segment, is_placeholder)`` pairs preserving order."""
    out: list[tuple[str, bool]] = []
    last_end = 0
    for m in _PLACEHOLDER_RE.finditer(text):
        if m.start() > last_end:
            out.append((text[last_end : m.start()], False))
        out.append((m.group(0), True))
        last_end = m.end()
    if last_end < len(text):
        out.append((text[last_end:], False))
    if not out:
        out.append((text, False))
    return out


def _is_header_only(text: str) -> bool:
    """True if every non-empty line starts with ``#`` — i.e. just headings."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return bool(lines) and all(ln.lstrip().startswith("#") for ln in lines)


class ChunkingService:
    """Async chunker for parsed markdown.

    Reusable across documents — instantiate once and share. Each call
    builds a per-invocation :class:`SemanticChunker` so its embedding
    adapter binds to the caller's event loop.
    """

    HEADER_LEVELS: ClassVar[list[tuple[str, str]]] = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]
    TARGET_CHUNK_SIZE_RANGE: ClassVar[tuple[int, int]] = (300, 800)
    MIN_CHUNK_SIZE: ClassVar[int] = 100
    MAX_CHUNK_SIZE: ClassVar[int] = 1500
    # SemanticChunker(buffer_size=1) 기본값 — combine_sentences가 prev+" "+cur+" "+next 형식
    # 으로 만드는 cache key를 정확히 복제해야 prewarm 히트가 보장된다.
    _SEMANTIC_BUFFER_SIZE: ClassVar[int] = 1
    _MERGE_JOIN: ClassVar[str] = "\n\n"

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        *,
        min_chars: int | None = None,
        max_chars: int | None = None,
        breakpoint_amount: int | None = None,
        prewarm: bool = True,
    ) -> None:
        self._embedding_client = embedding_client
        self._min_chars = min_chars if min_chars is not None else self.MIN_CHUNK_SIZE
        self._max_chars = max_chars if max_chars is not None else self.MAX_CHUNK_SIZE
        self._breakpoint_amount = (
            breakpoint_amount
            if breakpoint_amount is not None
            else settings.chunking_breakpoint_amount
        )
        # prewarm은 1.4MB급 문서에서 SemanticChunker 호출당 작은 배치로 누적되는 GC·메모리
        # 압박을 단발 큰 배치로 풀어주는 최적화. 캐시 키가 SemanticChunker가 만드는 것과
        # 동일해야 의미가 있다 — 동일 텍스트로 재호출 시 disk cache로 ms대 응답.
        self._prewarm = prewarm
        self._header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=self.HEADER_LEVELS,
            strip_headers=False,
        )
        self._hard_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._max_chars,
            chunk_overlap=100,
            separators=["\n\n", "\n", "。", ". ", " ", ""],
        )
        # 모델 두 번 로딩 방지 — BgeM3Client가 이미 가지고 있는 토크나이저를 재사용.
        self._tokenizer = embedding_client.tokenizer

    async def chunk_markdown(self, markdown: str, source_id: UUID) -> list[Chunk]:
        """Chunk a markdown document into a list of :class:`Chunk`.

        Args:
            markdown: Parsed markdown produced by the parser layer.
            source_id: Source document identifier — copied onto every chunk.

        Returns:
            Chunks in document order with contiguous ``chunk_index`` from 0.

        Raises:
            TablePlaceholderSplitError: A table placeholder ended up split
                across a chunk boundary.
        """
        logger.info(
            "chunking.start",
            source_id=str(source_id),
            markdown_length=len(markdown),
        )

        if not markdown.strip():
            logger.info("chunking.complete", source_id=str(source_id), chunk_count=0)
            return []

        masked, tables = _extract_tables(markdown)
        logger.info(
            "chunking.table_extracted",
            source_id=str(source_id),
            table_count=len(tables),
        )

        header_docs = self._header_splitter.split_text(masked)
        if not header_docs:
            logger.info("chunking.complete", source_id=str(source_id), chunk_count=0)
            return []

        if self._prewarm:
            await self._prewarm_embedding_cache(header_docs, source_id)

        loop = asyncio.get_running_loop()
        adapter = _LangChainEmbeddingsAdapter(self._embedding_client, loop)
        semantic_chunker = SemanticChunker(
            adapter,
            breakpoint_threshold_type="percentile",
            breakpoint_threshold_amount=self._breakpoint_amount,
        )

        raw_chunks: list[Chunk] = []
        for doc in header_docs:
            header_path = _build_header_path(doc.metadata)
            for segment, is_placeholder in _split_by_placeholder(doc.page_content):
                if is_placeholder:
                    idx = int(_PLACEHOLDER_RE.match(segment).group(1))  # type: ignore[union-attr]
                    raw_chunks.append(
                        self._make_chunk(
                            content=tables[idx],
                            chunk_type="table",
                            header_path=header_path,
                            source_id=source_id,
                            chunk_index=len(raw_chunks),
                        )
                    )
                    continue

                stripped = segment.strip()
                if not stripped or _is_header_only(stripped):
                    continue

                # 짧은 본문은 SemanticChunker 우회 — 어차피 한 청크로 합쳐지고 임베딩 비용 절약.
                if len(stripped) < self._min_chars:
                    pieces = [stripped]
                else:
                    pieces = await asyncio.to_thread(semantic_chunker.split_text, segment)

                for piece in pieces:
                    expanded = self._verify_no_split_placeholder(piece, source_id)
                    for fp in self._enforce_max([expanded]):
                        fp = fp.strip()
                        if not fp:
                            continue
                        raw_chunks.append(
                            self._make_chunk(
                                content=fp,
                                chunk_type="text",
                                header_path=header_path,
                                source_id=source_id,
                                chunk_index=len(raw_chunks),
                            )
                        )

        merged = self._merge_short(raw_chunks)

        for c in merged:
            if not (self.MIN_CHUNK_SIZE <= c.char_count <= self.MAX_CHUNK_SIZE):
                logger.warning(
                    "chunking.warning",
                    source_id=str(source_id),
                    reason="char_count_out_of_range",
                    chunk_index=c.chunk_index,
                    char_count=c.char_count,
                    chunk_type=c.metadata["chunk_type"],
                )

        logger.info(
            "chunking.complete",
            source_id=str(source_id),
            chunk_count=len(merged),
            total_chars=sum(c.char_count for c in merged),
        )
        return merged

    def _verify_no_split_placeholder(self, piece: str, source_id: UUID) -> str:
        """Raise if a placeholder is partially present; pass through otherwise.

        SemanticChunker splits at sentence boundaries (``[.?!]\\s+``) so
        a placeholder lacking those tokens is highly unlikely to be cut —
        but we still verify, because a corrupted table would survive
        downstream silently.
        """
        # `<<<TABLE_`로 시작하는 부분 문자열 수와 정확한 placeholder 매칭 수가 다르면 깨진 것.
        opens = piece.count("<<<TABLE_")
        closes = piece.count(">>>")
        full = len(_PLACEHOLDER_RE.findall(piece))
        if opens != full or closes != full:
            logger.error(
                "chunking.warning",
                source_id=str(source_id),
                reason="placeholder_split",
                fragment=piece[:200],
            )
            raise TablePlaceholderSplitError(
                f"Table placeholder partially present in chunk: {piece[:200]!r}"
            )
        return piece

    def _enforce_max(self, pieces: list[str]) -> list[str]:
        out: list[str] = []
        for p in pieces:
            if len(p) <= self._max_chars:
                out.append(p)
            else:
                out.extend(self._hard_splitter.split_text(p))
        return out

    @classmethod
    def _combined_sentences(cls, text: str) -> list[str]:
        """Replicate ``SemanticChunker(buffer_size=1)``'s pre-embedding sentence prep.

        The cache key must match what ``SemanticChunker`` will later request:
        ``prev_sentence + " " + current + " " + next_sentence`` for each i,
        with one-sided buffer at the edges. Empty splits are kept verbatim
        because LangChain does not filter them either.
        """
        single = _SENTENCE_SPLIT_RE.split(text)
        buf = cls._SEMANTIC_BUFFER_SIZE
        combined: list[str] = []
        for i in range(len(single)):
            parts: list[str] = [single[j] + " " for j in range(max(0, i - buf), i)]
            parts.append(single[i])
            parts.extend(" " + single[j] for j in range(i + 1, min(len(single), i + 1 + buf)))
            combined.append("".join(parts))
        return combined

    async def _prewarm_embedding_cache(self, header_docs: list, source_id: UUID) -> None:
        """Pre-embed every sentence SemanticChunker will later request.

        SemanticChunker가 header·placeholder로 쪼개진 segment마다 별도 호출을
        보내면 BGE-M3 dynamic batching이 작동할 여지가 없고(평균 30 texts/call)
        매 호출이 ONNX 활성 텐서를 새로 만들어 GC 페널티가 누적된다. 진입 시점에
        모든 combined_sentence를 단발 큰 배치로 던지면 디스크 캐시가 채워지고
        후속 SemanticChunker 호출은 모두 캐시 히트로 ms대 응답한다.
        """
        all_combined: list[str] = []
        for doc in header_docs:
            for segment, is_placeholder in _split_by_placeholder(doc.page_content):
                if is_placeholder:
                    continue
                stripped = segment.strip()
                if not stripped or _is_header_only(stripped):
                    continue
                # 짧은 segment는 chunk_markdown에서 SemanticChunker를 우회하므로 prewarm 대상 외.
                if len(stripped) < self._min_chars:
                    continue
                all_combined.extend(self._combined_sentences(segment))

        if not all_combined:
            return

        logger.info(
            "chunking.prewarm.start",
            source_id=str(source_id),
            sentence_count=len(all_combined),
        )
        await self._embedding_client.embed_batch(all_combined)
        logger.info(
            "chunking.prewarm.complete",
            source_id=str(source_id),
            sentence_count=len(all_combined),
        )

    def _estimate_tokens(self, text: str) -> int:
        # special_tokens 제외 — BGE-M3 [CLS][SEP] 두 개를 더 빼는 게 정확하지만 청킹용
        # 추정치라 무시할 만함.
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def _make_chunk(
        self,
        *,
        content: str,
        chunk_type: str,
        header_path: list[str],
        source_id: UUID,
        chunk_index: int,
    ) -> Chunk:
        return Chunk(
            content=content,
            char_count=len(content),
            chunk_index=chunk_index,
            source_id=source_id,
            metadata={
                "header_path": list(header_path),
                "chunk_type": chunk_type,
                "token_count_estimate": self._estimate_tokens(content),
                "has_numbers": _has_numbers(content),
                "has_proper_nouns": _has_proper_nouns(content),
            },
        )

    def _merge_short(self, chunks: list[Chunk]) -> list[Chunk]:
        if not chunks:
            return chunks
        result: list[Chunk] = []
        for c in chunks:
            can_merge = (
                bool(result)
                and c.metadata["chunk_type"] == "text"
                and result[-1].metadata["chunk_type"] == "text"
                and result[-1].metadata["header_path"] == c.metadata["header_path"]
                and len(result[-1].content) + len(self._MERGE_JOIN) + len(c.content)
                <= self._max_chars
                and (len(c.content) < self._min_chars or len(result[-1].content) < self._min_chars)
            )
            if can_merge:
                last = result[-1]
                merged_text = last.content + self._MERGE_JOIN + c.content
                result[-1] = Chunk(
                    content=merged_text,
                    char_count=len(merged_text),
                    chunk_index=last.chunk_index,
                    source_id=last.source_id,
                    metadata={
                        **last.metadata,
                        "token_count_estimate": self._estimate_tokens(merged_text),
                        "has_numbers": last.metadata["has_numbers"] or _has_numbers(c.content),
                        "has_proper_nouns": (
                            last.metadata["has_proper_nouns"] or _has_proper_nouns(c.content)
                        ),
                    },
                )
            else:
                result.append(
                    Chunk(
                        content=c.content,
                        char_count=c.char_count,
                        chunk_index=len(result),
                        source_id=c.source_id,
                        metadata=c.metadata,
                    )
                )
        return result
