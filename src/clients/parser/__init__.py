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
from src.clients.parser.hwpx import HwpxParser
from src.clients.parser.pdf import PdfParser
from src.clients.parser.registry import ParserRegistry

__all__ = [
    "HwpxParser",
    "ParseCache",
    "ParseResult",
    "ParserClient",
    "ParserRegistry",
    "PdfParser",
    "UnsupportedFormatError",
]
