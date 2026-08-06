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


class ParserRegistry:
    def __init__(self, parsers: list[ParserClient] | None = None) -> None:
        self._parsers: list[ParserClient] = parsers or [
            HwpxParser(),
            PdfParser(),
            DocxParser(),
            TextParser(),
        ]

    def register(self, parser: ParserClient) -> None:
        self._parsers.append(parser)

    def resolve(self, file_path: Path) -> ParserClient:
        for parser in self._parsers:
            if parser.supports(file_path):
                return parser
        raise UnsupportedFormatError(f"No parser for {file_path.suffix!r} ({file_path.name})")

    async def parse(self, file_path: Path) -> ParseResult:
        return await self.resolve(file_path).parse(file_path)
