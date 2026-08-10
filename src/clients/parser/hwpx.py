"""HWPX 어댑터.

python-hwpx의 exporter를 1차 경로로, TextExtractor 폴백을 2차 경로로 사용.
exporter import 실패 자체도 폴백 그물망에 포함되어야 해 ``from hwpx ...``
는 메서드 안에서 import.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import structlog

from src.clients.parser.base import (
    _TABLE_LINE,
    ParseCache,
    ParseMetadata,
    ParserClient,
    ParseResult,
    _filter_empty_tables,
    _measure_markdown,
    _strip_page_numbers,
    strip_replacement_chars,
)

logger = structlog.get_logger(__name__)


_HEADING_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^제\s*\d+\s*장\s+.+$"), "# "),
    (re.compile(r"^제\s*\d+\s*절\s+.+$"), "## "),
    (re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s+.+$"), "# "),
    (re.compile(r"^\d+\.\s+[가-힣].+$"), "### "),
    (re.compile(r"^[가나다라마바사아자차]\.\s+.+$"), "#### "),
]


def _apply_heading_rules(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_code = False
    for raw in lines:
        stripped = raw.lstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(raw)
            continue
        if in_code:
            out.append(raw)
            continue
        if stripped.startswith("#") or _TABLE_LINE.match(raw):
            out.append(raw)
            continue
        new_line = raw
        for pattern, prefix in _HEADING_RULES:
            if pattern.match(stripped):
                new_line = prefix + stripped.rstrip()
                break
        out.append(new_line)
    return out


class HwpxParser(ParserClient):
    EXTENSIONS = (".hwpx",)

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
            logger.info("hwpx.parse.cache_hit", path=str(path))
            return cached

        logger.info("hwpx.parse.started", path=str(path), size_bytes=path.stat().st_size)
        t0 = time.perf_counter()

        markdown, warnings, header_footer_removed = self._extract(path)
        markdown = self._postprocess_markdown(markdown)

        char_count, table_count, heading_count = _measure_markdown(markdown)
        warnings.extend(
            [
                "page_count_unavailable",
                "figure_count_deferred",
                "footnote_count_deferred",
            ]
        )

        result = ParseResult(
            source_path=path,
            markdown=markdown,
            metadata=ParseMetadata(
                page_count=None,
                char_count=char_count,
                table_count=table_count,
                heading_count=heading_count,
                header_footer_removed=header_footer_removed,
            ),
            warnings=warnings,
        )
        self.cache.store(path, result)

        logger.info(
            "hwpx.parse.completed",
            path=str(path),
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            char_count=char_count,
            table_count=table_count,
            heading_count=heading_count,
            header_footer_removed=header_footer_removed,
        )
        return result

    def _extract(self, path: Path) -> tuple[str, list[str], bool]:
        warnings: list[str] = []
        try:
            from hwpx import HwpxDocument
            from hwpx.tools.exporter import export_markdown

            doc = HwpxDocument.open(path)
            header_footer_removed = self._strip_header_footer(doc, warnings)
            markdown = export_markdown(doc, include_tables=True)
            return markdown, warnings, header_footer_removed
        except Exception as e:
            logger.warning(
                "hwpx.parse.fallback",
                reason="exporter_failed",
                error_type=type(e).__name__,
                error_message=str(e),
                path=str(path),
            )
            warnings.append(f"exporter_failed:{type(e).__name__}")
            markdown = self._fallback_text_extract(path)
            return markdown, warnings, False

    @staticmethod
    def _postprocess_markdown(markdown: str) -> str:
        markdown = strip_replacement_chars(_strip_page_numbers(markdown))
        lines = markdown.split("\n")
        lines = _apply_heading_rules(lines)
        markdown = "\n".join(lines)
        markdown = _filter_empty_tables(markdown)
        return markdown

    @staticmethod
    def _strip_header_footer(doc: object, warnings: list[str]) -> bool:
        removed = False
        for op_name in ("remove_header", "remove_footer"):
            op = getattr(doc, op_name, None)
            if op is None:
                warnings.append(f"{op_name}_unavailable")
                continue
            try:
                op()
                removed = True
            except Exception as e:
                logger.warning(
                    "hwpx.header_footer.strip_failed",
                    op=op_name,
                    error_type=type(e).__name__,
                )
                warnings.append(f"{op_name}_failed:{type(e).__name__}")
        return removed

    @staticmethod
    def _fallback_text_extract(path: Path) -> str:
        from hwpx import TextExtractor

        lines: list[str] = []
        with TextExtractor(str(path)) as extractor:
            for section in extractor.iter_sections():
                for para in extractor.iter_paragraphs(section):
                    text = para.text()
                    if text:
                        lines.append(text)
        return "\n\n".join(lines)
