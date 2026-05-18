from __future__ import annotations

import asyncio
import atexit
import hashlib
import queue
import re
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_PAGE_NUMBER_LINE = re.compile(
    r"^\s*(?:[-–—]\s*\d+\s*[-–—]|\d+\s*/\s*\d+|\d+)\s*$",
    re.MULTILINE,
)
_SEPARATOR_CELL = re.compile(r"^\s*:?-{2,}:?\s*$")

_HEADING_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^제\s*\d+\s*장\s+.+$"), "# "),
    (re.compile(r"^제\s*\d+\s*절\s+.+$"), "## "),
    (re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s+.+$"), "# "),
    (re.compile(r"^\d+\.\s+[가-힣].+$"), "### "),
    (re.compile(r"^[가나다라마바사아자차]\.\s+.+$"), "#### "),
]


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


class ParserError(Exception):
    pass


class UnsupportedFormatError(ParserError):
    pass


class ParserClient(ABC):
    @abstractmethod
    async def parse(self, file_path: Path) -> ParseResult: ...

    @abstractmethod
    def supports(self, file_path: Path) -> bool: ...


class ParseCache:
    def __init__(self, root: Path = Path("./cache/parsed")) -> None:
        self.root = root

    @staticmethod
    def _key(file_path: Path) -> str:
        abs_path = str(file_path.resolve())
        mtime = file_path.stat().st_mtime_ns
        raw = f"{abs_path}:{mtime}".encode()
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
        markdown = _strip_page_numbers(markdown)
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


_DOCLING_CONVERTER: object | None = None

# Daemon worker tracking — process exit 시 PdfDocument cleanup 충돌(SIGABRT) 완화용.
_active_docling_workers: list[threading.Thread] = []
_workers_lock = threading.Lock()


def _wait_for_active_workers() -> None:
    """Wait briefly for in-flight docling daemons at process exit.

    SIGABRT 회피: docling worker가 pymupdf로 PdfDocument를 open한 채로 process
    가 종료되면 interpreter shutdown 시 pymupdf cleanup이 충돌해 abort가 난다.
    2초 안에 끝나는 worker는 정상 cleanup, 못 끝나면 그냥 daemon으로 강제 종료.
    """
    deadline = time.monotonic() + 2.0
    with _workers_lock:
        workers = list(_active_docling_workers)
    for thread in workers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(timeout=remaining)


atexit.register(_wait_for_active_workers)


def _get_docling_converter() -> object:
    """Lazily build a process-wide DocumentConverter with OCR disabled.

    OCR (RapidOCR) is off because Korean government PDFs are overwhelmingly
    text-based — running OCR per page added ~1-2s/page and pushed even a
    53-page 1.9MB PDF past the 60s timeout in our measurements. Scanned
    PDFs that need OCR are out of scope for this parser; the fallback
    (pymupdf4llm) also does not OCR. Single instance per process so PDF
    jobs do not repay the ~5-10s converter init cost.
    """
    global _DOCLING_CONVERTER
    if _DOCLING_CONVERTER is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        # AUTO: CUDA 가용 시 GPU, 아니면 CPU 폴백 — 머신 종속 코드를 두지 않기 위해.
        pipeline_options = PdfPipelineOptions(
            do_ocr=False,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.AUTO),
        )
        _DOCLING_CONVERTER = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
    return _DOCLING_CONVERTER


def _docling_convert(file_path: Path) -> tuple[str, int | None, int]:
    """Run docling and return (markdown, page_count, image_count)."""
    converter = _get_docling_converter()
    result = converter.convert(file_path)  # type: ignore[attr-defined]
    doc = result.document
    markdown = doc.export_to_markdown()
    page_count = len(doc.pages) if getattr(doc, "pages", None) is not None else None
    image_count = len(doc.pictures) if getattr(doc, "pictures", None) is not None else 0
    return markdown, page_count, image_count


def _pymupdf_convert(file_path: Path) -> tuple[str, int | None, int]:
    """Fallback path: pymupdf4llm for markdown, pymupdf for page/image counts."""
    import pymupdf
    import pymupdf4llm

    markdown = pymupdf4llm.to_markdown(str(file_path))
    src = pymupdf.open(str(file_path))
    try:
        page_count = src.page_count
        image_count = sum(len(p.get_images()) for p in src)
    finally:
        src.close()
    return markdown, page_count, image_count


class PdfParser(ParserClient):
    """PDF parser backed by docling, with pymupdf4llm as a fallback.

    Three size buckets (measured on Korean government PDFs; medium ceiling
    raised from 5 MB after enabling GPU acceleration via docling's
    AcceleratorDevice.AUTO):

    - **small (< 1 MB)**: try docling with a 180s timeout. Falls back to
      pymupdf4llm on timeout/error.
    - **medium (1 MB ≤ size < 13 MB)**: try docling with a 600s timeout.
      With CUDA available, a 12.5 MB / 368-page sample completes in ~234s
      (≈0.64 s/page). CPU-only runs are an order of magnitude slower and
      rely on the timeout as a safety net.
    - **large (≥ 13 MB)**: skip docling entirely and go straight to
      pymupdf4llm. Even with GPU, dense statistical reports stall past
      this size; the cost of trying still consumes the full ceiling
      before we give up.

    docling runs in a **daemon thread** with queue-based polling rather
    than ``asyncio.to_thread`` + ``wait_for``. The default executor used
    by ``to_thread`` is joined on ``asyncio.run()`` shutdown, so a
    still-running docling worker after a timeout would block the main
    caller for the remaining minutes of the conversion. Daemon threads
    are invisible to that join and are collected at process exit.
    """

    EXTENSIONS: ClassVar[tuple[str, ...]] = (".pdf",)
    # 크기 분기 — GPU 가속 후 12.5MB/368p가 234초로 통과해 medium 상한을 5MB→13MB로 상향.
    # 17MB 표/차트 밀집 PDF는 GPU에서도 15분 timeout 안에 안 끝나 large로 fallback.
    SMALL_PDF_MAX_BYTES: ClassVar[int] = 1 * 1024 * 1024
    MEDIUM_PDF_MAX_BYTES: ClassVar[int] = 13 * 1024 * 1024
    # timeout은 CPU-only 환경에서 무한 대기를 막는 안전망 (GPU 환경은 훨씬 일찍 끝남).
    SMALL_PDF_TIMEOUT_S: ClassVar[float] = 180.0
    MEDIUM_PDF_TIMEOUT_S: ClassVar[float] = 600.0
    # daemon thread polling 주기 — 너무 짧으면 GIL 압박, 너무 길면 timeout latency.
    _POLL_INTERVAL_S: ClassVar[float] = 0.1

    def __init__(
        self,
        *,
        cache: ParseCache | None = None,
        small_pdf_max_bytes: int | None = None,
        medium_pdf_max_bytes: int | None = None,
        small_pdf_timeout_s: float | None = None,
        medium_pdf_timeout_s: float | None = None,
    ) -> None:
        self.cache = cache or ParseCache()
        # 인스턴스 오버라이드는 단위 테스트에서 timeout=0.001로 강제 폴백 경로를 자극하기 위함.
        self._small_pdf_max_bytes = (
            small_pdf_max_bytes if small_pdf_max_bytes is not None else self.SMALL_PDF_MAX_BYTES
        )
        self._medium_pdf_max_bytes = (
            medium_pdf_max_bytes if medium_pdf_max_bytes is not None else self.MEDIUM_PDF_MAX_BYTES
        )
        self._small_pdf_timeout_s = (
            small_pdf_timeout_s if small_pdf_timeout_s is not None else self.SMALL_PDF_TIMEOUT_S
        )
        self._medium_pdf_timeout_s = (
            medium_pdf_timeout_s if medium_pdf_timeout_s is not None else self.MEDIUM_PDF_TIMEOUT_S
        )

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.EXTENSIONS

    async def parse(self, file_path: Path) -> ParseResult:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(path)

        cached = self.cache.load(path)
        if cached is not None:
            logger.info("pdf.parse.cache_hit", path=str(path))
            return cached

        size_bytes = path.stat().st_size
        logger.info("pdf.parse.started", path=str(path), size_bytes=size_bytes)
        t0 = time.perf_counter()

        warnings: list[str] = []
        markdown, page_count, image_count = await self._convert(
            path=path,
            size_bytes=size_bytes,
            warnings=warnings,
        )
        markdown = self._postprocess(markdown)
        char_count, table_count, heading_count = _measure_markdown(markdown)

        # pymupdf4llm 경로(fallback + skip_too_large)는 표가 평문으로 추출됨 — 호출자가
        # 메타 신뢰도를 낮출 수 있도록 명시. 두 시그널을 한 곳에 모아 분기 누락을 방지.
        used_pymupdf = any(
            w.startswith("fallback_to_pymupdf4llm") or w == "skip_docling_too_large"
            for w in warnings
        )
        if used_pymupdf:
            warnings.append("fallback_tables_as_plaintext")

        result = ParseResult(
            source_path=path,
            markdown=markdown,
            metadata=ParseMetadata(
                page_count=page_count,
                char_count=char_count,
                table_count=table_count,
                heading_count=heading_count,
                image_count=image_count,
                header_footer_removed=False,
            ),
            warnings=warnings,
        )
        self.cache.store(path, result)

        logger.info(
            "pdf.parse.completed",
            path=str(path),
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            page_count=page_count,
            char_count=char_count,
            table_count=table_count,
            image_count=image_count,
            warnings=warnings,
        )
        return result

    async def _convert(
        self,
        *,
        path: Path,
        size_bytes: int,
        warnings: list[str],
    ) -> tuple[str, int | None, int]:
        """Dispatch by file size; fall back to pymupdf4llm on docling timeout/error.

        Returns:
            ``(markdown, page_count, image_count)``. Mutates ``warnings``
            in place to record skip/fallback reasons.
        """
        if size_bytes >= self._medium_pdf_max_bytes:
            # 5MB+ PDF는 docling이 사실상 끝나지 않음 — 시도 비용도 아끼고 바로 pymupdf로.
            logger.info(
                "pdf.parse.docling.skip_too_large",
                path=str(path),
                size_bytes=size_bytes,
                reason="docling impractical for 5MB+ PDFs",
            )
            warnings.append("skip_docling_too_large")
            return _pymupdf_convert(path)

        timeout_s = (
            self._small_pdf_timeout_s
            if size_bytes < self._small_pdf_max_bytes
            else self._medium_pdf_timeout_s
        )

        try:
            return await self._docling_with_daemon_timeout(path, timeout_s)
        except TimeoutError:
            logger.warning(
                "pdf.parse.docling_timeout",
                path=str(path),
                timeout_sec=timeout_s,
                size_bytes=size_bytes,
            )
            warnings.append("fallback_to_pymupdf4llm:timeout")
            return _pymupdf_convert(path)
        except Exception as e:
            logger.warning(
                "pdf.parse.docling_error",
                path=str(path),
                error_type=type(e).__name__,
                error_message=str(e),
            )
            warnings.append(f"fallback_to_pymupdf4llm:{type(e).__name__}")
            return _pymupdf_convert(path)

    async def _docling_with_daemon_timeout(
        self,
        path: Path,
        timeout_s: float,
    ) -> tuple[str, int | None, int]:
        """Run docling on a daemon thread, polling a queue with a deadline.

        Why not ``asyncio.to_thread`` + ``asyncio.wait_for``: the to_thread
        worker lives in the loop's default executor, and ``asyncio.run()``
        shutdown waits for that executor's threads to join. A still-running
        docling worker therefore pins the main caller for the rest of the
        conversion (~minutes) after timeout — observed as a 287s post-parse
        hang in measurements. A daemon thread is opaque to that shutdown
        and is collected when the process exits.
        """
        q: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                q.put(("ok", _docling_convert(path)))
            except BaseException as exc:  # SystemExit/KeyboardInterrupt도 호출자에게 전달
                q.put(("err", exc))

        t = threading.Thread(target=worker, daemon=True, name="pdf-docling")
        # atexit hook이 process 종료 직전에 in-flight worker join을 시도하도록 등록.
        with _workers_lock:
            _active_docling_workers.append(t)
        t.start()

        try:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"docling timeout after {timeout_s:.0f}s")
                    # 폴링 사이에 다른 asyncio 작업이 진행할 수 있도록 짧게 양보.
                    await asyncio.sleep(self._POLL_INTERVAL_S)
                    continue
                if kind == "err":
                    assert isinstance(payload, BaseException)
                    raise payload
                assert isinstance(payload, tuple)
                return payload  # type: ignore[return-value]
        finally:
            # 정상 완료/timeout 모두 — atexit이 굳이 기다리지 않도록 추적 해제.
            # 단, timeout으로 빠져나오는 경우 thread는 계속 daemon으로 살아있음.
            with _workers_lock:
                if t in _active_docling_workers and not t.is_alive():
                    _active_docling_workers.remove(t)

    @staticmethod
    def _postprocess(markdown: str) -> str:
        # 페이지 번호 줄 제거 + 빈 표 블록 제거 (HwpxParser 후처리와 동일 정책으로 일관성 유지).
        markdown = _strip_page_numbers(markdown)
        markdown = _filter_empty_tables(markdown)
        return markdown


class ParserRegistry:
    def __init__(self, parsers: list[ParserClient] | None = None) -> None:
        self._parsers: list[ParserClient] = parsers or [HwpxParser(), PdfParser()]

    def register(self, parser: ParserClient) -> None:
        self._parsers.append(parser)

    def resolve(self, file_path: Path) -> ParserClient:
        for parser in self._parsers:
            if parser.supports(file_path):
                return parser
        raise UnsupportedFormatError(f"No parser for {file_path.suffix!r} ({file_path.name})")

    async def parse(self, file_path: Path) -> ParseResult:
        return await self.resolve(file_path).parse(file_path)
