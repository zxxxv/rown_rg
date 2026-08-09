"""DOCX 어댑터 — docling(Word 파이프라인).

PDF와 같은 docling 계열이지만 DOCX는 레이아웃 모델·OCR이 필요 없어 빠르고 가볍다(XML 추출).
import가 무거워 호출 시점까지 지연한다. PDF처럼 수 분씩 걸리지 않으므로 데몬 스레드 타임아웃
기계장치 대신 to_thread + wait_for 안전망만 둔다.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import ClassVar

import structlog

from src.clients.parser.base import (
    ParseCache,
    ParseMetadata,
    ParserClient,
    ParseResult,
    _filter_empty_tables,
    _measure_markdown,
    _strip_page_numbers,
)

logger = structlog.get_logger(__name__)

_DOCX_CONVERTER: object | None = None


def _get_docx_converter() -> object:
    """프로세스 1회 DocumentConverter(DOCX 전용). 최초 호출 시 docling 초기화(무거움).

    allowed_formats를 DOCX로 좁혀 PDF 파이프라인(레이아웃/OCR 모델)을 로드하지 않는다.
    PDF용 변환기(pdf.py)와 별도 인스턴스 — 서로 다른 파이프라인이라 섞지 않는다.
    """
    global _DOCX_CONVERTER
    if _DOCX_CONVERTER is None:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter

        _DOCX_CONVERTER = DocumentConverter(allowed_formats=[InputFormat.DOCX])
    return _DOCX_CONVERTER


def _docx_convert(file_path: Path) -> tuple[str, int]:
    """docling으로 DOCX→markdown. (markdown, image_count) 반환."""
    converter = _get_docx_converter()
    result = converter.convert(file_path)  # type: ignore[attr-defined]
    doc = result.document
    markdown = doc.export_to_markdown()
    image_count = len(doc.pictures) if getattr(doc, "pictures", None) is not None else 0
    return markdown, image_count


class DocxParser(ParserClient):
    EXTENSIONS: ClassVar[tuple[str, ...]] = (".docx",)
    # DOCX는 XML 추출이라 초 단위로 끝난다 — 병적인 파일의 무한 대기만 막는 안전망.
    CONVERT_TIMEOUT_S: ClassVar[float] = 120.0

    def __init__(self, cache: ParseCache | None = None) -> None:
        self.cache = cache or ParseCache()

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.EXTENSIONS

    async def parse(self, file_path: Path) -> ParseResult:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(path)

        cached = self.cache.load(path)
        if cached is not None:
            logger.info("docx.parse.cache_hit", path=str(path))
            return cached

        logger.info("docx.parse.started", path=str(path), size_bytes=path.stat().st_size)
        t0 = time.perf_counter()

        markdown, image_count = await asyncio.wait_for(
            asyncio.to_thread(_docx_convert, path), timeout=self.CONVERT_TIMEOUT_S
        )
        markdown = self._postprocess(markdown)
        char_count, table_count, heading_count = _measure_markdown(markdown)

        result = ParseResult(
            source_path=path,
            markdown=markdown,
            metadata=ParseMetadata(
                page_count=None,
                char_count=char_count,
                table_count=table_count,
                heading_count=heading_count,
                image_count=image_count,
            ),
            warnings=[],
        )
        self.cache.store(path, result)

        logger.info(
            "docx.parse.completed",
            path=str(path),
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            char_count=char_count,
            table_count=table_count,
            image_count=image_count,
        )
        return result

    @staticmethod
    def _postprocess(markdown: str) -> str:
        # HWPX/PDF 후처리와 동일 정책으로 일관성 유지.
        markdown = _strip_page_numbers(markdown)
        markdown = _filter_empty_tables(markdown)
        return markdown
