"""Parser package primitives.

Models, errors, the ``ParserClient`` ABC, shared markdown post-processing
helpers, and the file-based ``ParseCache``. This module has no parser-
library dependencies (python-hwpx, docling): adapter modules pull in those
heavy imports themselves so importing :mod:`src.clients.parser` stays cheap.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# 페이지 경계 마커 - PDF 파서가 페이지 사이에 심고, 색인이 읽어 청크에 페이지 번호를
# 단 뒤 제거한다(services/indexing/_pages.py). 청크·화면·프롬프트에는 절대 남지 않는다.
# HTML 주석 꼴이라 _strip_page_numbers(숫자 단독 줄)·표 필터에 걸리지 않는다.
PAGE_BREAK_MARKER = "<!-- rown:page-break -->"


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_PAGE_NUMBER_LINE = re.compile(
    r"^\s*(?:[-–—]\s*\d+\s*[-–—]|\d+\s*/\s*\d+|\d+)\s*$",
    re.MULTILINE,
)
_SEPARATOR_CELL = re.compile(r"^\s*:?-{2,}:?\s*$")


class ParseMetadata(BaseModel):
    page_count: int | None = None
    char_count: int = 0
    table_count: int = 0
    heading_count: int = 0
    image_count: int = 0
    header_footer_removed: bool = False


class ParseResult(BaseModel):
    source_path: Path
    markdown: str
    metadata: ParseMetadata = Field(default_factory=ParseMetadata)
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False
    # 어느 파서가 이 결과를 만들었나 - "docling-remote" | "docling-local" | "pymupdf" |
    # "hwpx" 등. 두 군데서 읽는다: (1) 색인이 project_sources 메타로 영속해 화면에
    # 저품질 파싱을 드러내고, (2) 캐시 히트 시 pymupdf 결과면 재파싱 자격을 판단한다.
    # 빈 문자열은 v3 이전 캐시본(발생하면 그대로 신뢰).
    parser_name: str = ""


class ParserError(Exception):
    pass


class UnsupportedFormatError(ParserError):
    pass


class ParserClient(ABC):
    @abstractmethod
    async def parse(self, file_path: Path) -> ParseResult: ...

    @abstractmethod
    def supports(self, file_path: Path) -> bool: ...


def _count_table_blocks(markdown: str) -> int:
    lines = markdown.split("\n")
    n = len(lines)
    count = 0
    i = 0
    while i < n:
        if _TABLE_LINE.match(lines[i]):
            count += 1
            while i < n and _TABLE_LINE.match(lines[i]):
                i += 1
            continue
        i += 1
    return count


def _measure_markdown(markdown: str) -> tuple[int, int, int]:
    char_count = len(markdown)
    table_count = _count_table_blocks(markdown)
    heading_count = sum(1 for _ in _HEADING.finditer(markdown))
    return char_count, table_count, heading_count


def _strip_page_numbers(markdown: str) -> str:
    return _PAGE_NUMBER_LINE.sub("", markdown)


# 글리프를 유니코드로 못 되돌린 자리에 남는 대체 문자(U+FFFD, ). PDF 추출에서
# 흔하다(실측 2026-08-10: 정부 PDF 본문의 최대 2.4%). 화면에 보기 흉할 뿐 아니라
# 임베딩·인용 원문에도 그대로 들어가므로 파싱 단계에서 걷어낸다.
_REPLACEMENT_CHAR = "�"
# 한 줄이 이 비율 이상 깨졌으면 남은 글자도 못 믿는다 — 줄째로 버린다.
_LINE_GARBLED_RATIO = 0.3


def strip_replacement_chars(markdown: str) -> str:
    """대체 문자 제거. 심하게 깨진 줄은 통째로 버린다(부분 제거는 단어를 뭉갠다)."""
    if _REPLACEMENT_CHAR not in markdown:
        return markdown
    out: list[str] = []
    for line in markdown.split(chr(10)):
        bad = line.count(_REPLACEMENT_CHAR)
        if not bad:
            out.append(line)
            continue
        stripped = line.strip()
        if stripped and bad / len(stripped) >= _LINE_GARBLED_RATIO:
            continue
        out.append(line.replace(_REPLACEMENT_CHAR, ""))
    return chr(10).join(out)


def _extract_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.match(c) for c in cells)


def _is_meaningful_table(block: list[str]) -> bool:
    rows = [_extract_cells(line) for line in block]
    if not rows:
        return False
    sep_idx: int | None = None
    for i, cells in enumerate(rows):
        if _is_separator_row(cells):
            sep_idx = i
            break
    if sep_idx is None:
        return True
    header_rows = rows[:sep_idx]
    data_rows = rows[sep_idx + 1 :]
    if not data_rows:
        return False
    total_text = "".join(c for row in (header_rows + data_rows) for c in row).strip()
    return len(total_text) >= 5


def _filter_empty_tables(markdown: str) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _TABLE_LINE.match(lines[i]):
            start = i
            while i < n and _TABLE_LINE.match(lines[i]):
                i += 1
            block = lines[start:i]
            if _is_meaningful_table(block):
                out.extend(block)
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


class ParseCache:
    def __init__(self, root: Path = Path("./cache/parsed")) -> None:
        self.root = root

    # 파서 출력 규약이 바뀌면 올린다 - 키에 섞여 옛 캐시를 자연 무효화한다.
    # v2: PDF 페이지 경계 마커 도입(2026-08-14). 마커 없는 캐시본이 재색인에 쓰이면
    # 그 자료만 페이지 없이 색인돼 "왜 이 자료만 점프가 안 되나"가 된다.
    # v3: parser_name 기록(2026-08-20). pymupdf 폴백 결과가 캐시에 박혀 docling이
    # 가능해진 뒤에도 저품질본이 영원히 재사용되던 구멍을 막는 전제 - 정체가
    # 없는 v2 캐시본으로는 재파싱 자격을 판단할 수 없다. hwpx/docx 캐시가 함께
    # 1회 무효화되는 비용은 감수한다(클래스 공유 상수).
    _VERSION = "v3"

    @staticmethod
    def _key(file_path: Path) -> str:
        abs_path = str(file_path.resolve())
        mtime = file_path.stat().st_mtime_ns
        raw = f"{abs_path}:{mtime}:{ParseCache._VERSION}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, file_path: Path) -> ParseResult | None:
        try:
            key = self._key(file_path)
        except FileNotFoundError:
            return None
        cache_file = self._path_for(key)
        if not cache_file.exists():
            return None
        try:
            data = cache_file.read_text(encoding="utf-8")
            result = ParseResult.model_validate_json(data)
        except Exception as e:
            logger.warning(
                "parser_cache.load_failed",
                cache_file=str(cache_file),
                error_type=type(e).__name__,
            )
            return None
        result.cached = True
        return result

    def store(self, file_path: Path, result: ParseResult) -> None:
        try:
            key = self._key(file_path)
        except FileNotFoundError:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        cache_file = self._path_for(key)
        payload = result.model_copy(update={"cached": False})
        cache_file.write_text(payload.model_dump_json(), encoding="utf-8")
