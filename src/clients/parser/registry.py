"""Extension-based dispatch over parser adapters."""

from __future__ import annotations

from pathlib import Path

from src.clients.parser.base import (
    ParserClient,
    ParseResult,
    UnsupportedFormatError,
)
from src.clients.parser.docx import DocxParser
from src.clients.parser.hwpx import HwpxParser
from src.clients.parser.pdf import PdfParser
from src.clients.parser.text import TextParser

# 등록된 파서 = 지원 포맷의 단일 진실. 업로드 허용 확장자도 여기서 파생한다.
_DEFAULT_PARSER_CLASSES: tuple[type[ParserClient], ...] = (
    HwpxParser,
    PdfParser,
    DocxParser,
    TextParser,
)

# 업로드/색인이 실제로 처리할 수 있는 확장자 집합(소문자, 점 포함) — 예: {".pdf", ".docx", ...}
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    ext for cls in _DEFAULT_PARSER_CLASSES for ext in getattr(cls, "EXTENSIONS", ())
)


class ParserRegistry:
    def __init__(self, parsers: list[ParserClient] | None = None) -> None:
        self._parsers: list[ParserClient] = parsers or [cls() for cls in _DEFAULT_PARSER_CLASSES]

    def register(self, parser: ParserClient) -> None:
        self._parsers.append(parser)

    def resolve(self, file_path: Path) -> ParserClient:
        for parser in self._parsers:
            if parser.supports(file_path):
                return parser
        raise UnsupportedFormatError(f"No parser for {file_path.suffix!r} ({file_path.name})")

    async def parse(self, file_path: Path) -> ParseResult:
        return await self.resolve(file_path).parse(file_path)
