"""Public API for the parser package.

Single-source import path. Submodules (base/hwpx/pdf/registry) are package-
internal — do not import from them directly.
"""

from src.clients.parser.base import (
    ParseCache,
    ParserClient,
    ParseResult,
    UnsupportedFormatError,
)
from src.clients.parser.docx import DocxParser
from src.clients.parser.hwpx import HwpxParser
from src.clients.parser.pdf import PdfParser
from src.clients.parser.registry import ParserRegistry
from src.clients.parser.text import TextParser

__all__ = [
    "DocxParser",
    "HwpxParser",
    "ParseCache",
    "ParseResult",
    "ParserClient",
    "ParserRegistry",
    "PdfParser",
    "TextParser",
    "UnsupportedFormatError",
]
