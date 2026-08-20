"""플레인 텍스트·마크다운 어댑터.

.md/.markdown은 이미 마크다운이라 그대로, .txt는 플레인 텍스트를 그대로 본문으로 쓴다
(청킹이 문단 단위로 처리). 외부 파서 라이브러리가 없어 import가 가볍다 — HWPX/PDF와 달리
바이트를 디코드만 하면 된다. 한글 문서 대비 UTF-8 실패 시 CP949(euc-kr)로 폴백한다.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from src.clients.parser.base import (
    ParseCache,
    ParseMetadata,
    ParserClient,
    ParseResult,
    _measure_markdown,
)

logger = structlog.get_logger(__name__)


class TextParser(ParserClient):
    EXTENSIONS = (".md", ".markdown", ".txt")

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
            logger.info("text.parse.cache_hit", path=str(path))
            return cached

        t0 = time.perf_counter()
        raw = path.read_bytes()
        warnings: list[str] = []
        try:
            markdown = raw.decode("utf-8")
        except UnicodeDecodeError:
            markdown = raw.decode("cp949", errors="replace")
            warnings.append("decoded_as_cp949")

        char_count, table_count, heading_count = _measure_markdown(markdown)
        result = ParseResult(
            source_path=path,
            parser_name="text",
            markdown=markdown,
            metadata=ParseMetadata(
                page_count=None,
                char_count=char_count,
                table_count=table_count,
                heading_count=heading_count,
            ),
            warnings=warnings,
        )
        self.cache.store(path, result)

        logger.info(
            "text.parse.completed",
            path=str(path),
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            char_count=char_count,
        )
        return result
