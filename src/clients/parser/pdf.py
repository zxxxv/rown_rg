"""PDF 어댑터 — docling 1차, pymupdf4llm 폴백.

docling import는 무거워 호출 시점까지 지연.
"""

from __future__ import annotations

import asyncio
import atexit
import queue
import threading
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
    strip_replacement_chars,
)

logger = structlog.get_logger(__name__)


_DOCLING_CONVERTER: object | None = None
# docling을 이 프로세스에서 못 쓰는 이유(시스템 라이브러리 부재 등). 한 번 확인되면
# 다시 시도하지 않는다 — 매 PDF마다 초기화에 실패하며 15초씩 버렸다(2026-08-10 실측:
# libxcb1 미설치 환경). 파일별 실패(타임아웃 등)는 여기 해당하지 않는다.
_DOCLING_UNAVAILABLE: str | None = None

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


def _detach_abandoned_worker(
    worker: threading.Thread, q: queue.Queue[tuple[str, object]], path: Path
) -> None:
    """타임아웃으로 버려진 docling 워커의 결과를 버리는 정리 스레드를 띄운다.

    워커 자체는 중간에 못 죽인다(docling 내부라 취소 지점이 없다). 하지만 워커가
    끝난 뒤 **결과를 붙들고 있는 것**은 막을 수 있다 - 큐에 들어온 마크다운·문서
    객체를 즉시 꺼내 버리면 그 참조가 사라진다.

    정리 스레드도 데몬이라 종료를 막지 않는다. 하는 일은 큐에서 한 번 꺼내 버리는
    것뿐이라 비용이 없다.
    """

    def drain() -> None:
        try:
            kind, _payload = q.get()  # 워커가 끝날 때까지 대기 후 결과를 버린다
            logger.info(
                "pdf.parse.abandoned_worker_drained",
                path=str(path),
                kind=kind,
            )
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=drain, daemon=True, name="pdf-docling-drain").start()


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


def _disable_docling(reason: str, detail: str) -> None:
    """이 프로세스에서 docling 사용을 끈다 — 이후 PDF는 곧장 pymupdf로."""
    global _DOCLING_UNAVAILABLE
    _DOCLING_UNAVAILABLE = reason
    logger.warning("pdf.parse.docling_disabled_for_process", reason=reason, error_message=detail)


def _is_environment_failure(exc: BaseException) -> bool:
    """이 프로세스에서 docling을 아예 못 쓰는 실패인가(파일별 실패와 구분).

    시스템 공유 라이브러리 부재(libxcb1 등)나 import 실패는 파일을 바꿔도 똑같이
    난다. 이걸 파일별 실패로 취급하면 업로드마다 초기화에 15초씩 버린다.
    """
    if isinstance(exc, ImportError | ModuleNotFoundError):
        return True
    if isinstance(exc, OSError) and "cannot open shared object file" in str(exc):
        return True
    return False


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

        if _DOCLING_UNAVAILABLE is not None:
            warnings.append(f"skip_docling_unavailable:{_DOCLING_UNAVAILABLE}")
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
            if _is_environment_failure(e):
                # 환경 문제는 다음 파일에서도 똑같이 난다 — 프로세스 내내 건너뛴다.
                _disable_docling(type(e).__name__, str(e))
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
            # 추적 목록에서 **항상** 뺀다. 예전에는 살아 있는 스레드를 남겨 뒀는데,
            # 그러면 타임아웃된 워커가 프로세스 수명 내내 리스트에 붙들려 GC가 안 된다.
            # 서버는 끝나지 않으므로 "프로세스 종료 때 정리된다"는 전제가 성립하지 않는다
            # (2026-08-12: 완성된 런 이후에도 anon 29.3GB가 그대로였다).
            with _workers_lock:
                if t in _active_docling_workers:
                    _active_docling_workers.remove(t)
            # 타임아웃으로 빠져나온 워커는 아직 변환 중이다. 놔두면 9.6MB PDF의 페이지·
            # 레이아웃 활성값을 든 채로 끝까지 돌고, 완성 결과를 아무도 안 읽는 큐에
            # 넣어 그것까지 남는다. 큐를 비워 두는 정리 콜백을 달아 워커가 끝나는 즉시
            # 결과를 버리게 한다 - 스레드 자체는 중간에 못 죽이지만(외부 라이브러리),
            # 최소한 결과와 큐가 영구 보관되지는 않는다.
            if t.is_alive():
                _detach_abandoned_worker(t, q, path)

    @staticmethod
    def _postprocess(markdown: str) -> str:
        # HwpxParser 후처리와 동일 정책으로 일관성 유지.
        markdown = strip_replacement_chars(_strip_page_numbers(markdown))
        markdown = _filter_empty_tables(markdown)
        return markdown
