"""PDF 파싱(docling) 서비스 — 변환을 CUDA로, 실패는 밖으로 드러낸다.

앱 서버에서 docling을 돌리면 메모리가 몰릴 때 죽고 **조용히 pymupdf로 떨어져**
표 구조가 사라진다(2026-08-20 실사고: 13건 중 9건). 여기로 옮기면 그 폴백 계급
자체가 없어진다 — 이 서비스는 실패하면 폴백하지 않고 실패를 돌려주고, 무엇으로
할지는 앱 쪽 클라이언트가 정한다.

**전용 레인.** 파싱은 건당 수십 초~수 분이라 리랭킹(1.5초)과 같은 큐를 쓰면
검색이 수 분 막히고, 큐의 처리 시간 지수이동평균이 오염돼 리랭킹 요청이 허위
429를 맞는다. 그래서 자체 GpuQueue 인스턴스를 받는다 - 예상 대기 거절·카운터를
그대로 얻으면서 통계는 격리된다.

**서버 쪽 데드라인.** 클라이언트가 타임아웃해도 여기서 데드라인이 없으면 변환이
레인을 계속 점유한다(좀비) — 이후 모든 파싱이 죽은 작업 뒤에 줄을 서서 429를
맞는다. 데드라인은 클라이언트 read 타임아웃(600초)보다 짧게(570초) 잡아
클라이언트가 연결 절단 대신 깔끔한 504를 받게 한다.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from gpu_service.app.config import ServiceConfig
from gpu_service.app.gpu_queue import GpuQueue

logger = logging.getLogger("gpu_service")

# 데몬 스레드 결과 큐 폴링 간격. 앱 pdf.py의 값과 같은 근거 - 짧을수록 데드라인
# 반응이 빠르지만 이벤트 루프를 자주 깨운다.
_POLL_INTERVAL_S = 0.2


class ParseTimeout(Exception):
    """서버 데드라인 초과 — 호출부가 504로 변환한다."""


class ClientGone(Exception):
    """클라이언트가 응답을 기다리다 떠남 — 레인만 반납하고 조용히 정리한다."""


class ParseService:
    """docling 변환기 보유 + 전용 레인 직렬화.

    변환기는 프로세스에 1개다(초기화 ~5-10초를 매 요청이 내지 않게). 레인이
    동시 1이라 변환기 동시 호출도 없다.
    """

    def __init__(self, config: ServiceConfig, *, lane: GpuQueue) -> None:
        self._config = config
        self._lane = lane
        self._converter: Any = None
        self._docling_version: str = ""

    def load(self) -> None:
        """뜰 때 변환기를 만든다. 실패하면 raise — 컨테이너가 조용히 절름발이로
        사는 것보다 부팅에서 죽어 알아차리는 편이 낫다(리랭커 load()와 같은 정책).
        """
        import docling
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        device = (
            AcceleratorDevice.CUDA if self._config.parse_device == "cuda" else AcceleratorDevice.CPU
        )
        # artifacts_path: 이미지가 HF_HUB_OFFLINE=1이라 가중치를 받으러 나가면 즉시
        # 죽는다. 미리 내려받은 폴더를 반드시 가리켜야 한다.
        # do_ocr=False: 앱 쪽과 같은 근거(한국 정부 PDF는 텍스트 기반, OCR은 페이지당
        # 1-2초) - 켜려면 앱과 여기의 출력이 갈라지지 않게 양쪽을 같이 바꿔야 한다.
        pipeline_options = PdfPipelineOptions(
            do_ocr=False,
            artifacts_path=self._config.parse_artifacts_dir,
            accelerator_options=AcceleratorOptions(device=device),
        )
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        self._docling_version = getattr(docling, "__version__", "unknown")
        logger.info(
            "parse converter ready: docling=%s device=%s artifacts=%s",
            self._docling_version,
            self._config.parse_device,
            self._config.parse_artifacts_dir,
        )

    @property
    def ready(self) -> bool:
        return self._converter is not None

    @property
    def device(self) -> str:
        return self._config.parse_device

    @property
    def docling_version(self) -> str:
        return self._docling_version

    async def parse(
        self,
        tmp_path: Path,
        page_break_placeholder: str,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> dict[str, Any]:
        """PDF 1건을 마크다운으로. 레인 → 데몬 스레드 → 데드라인·이탈 폴링.

        page_break_placeholder를 폼으로 받는 이유: 페이지 마커 계약은 앱 소유다.
        이 컨테이너는 앱의 base.py를 import할 수 없고, 상수를 복제하면 언젠가
        어긋나 페이지 점프가 조용히 깨진다.
        """
        async with self._lane.acquire():
            t0 = time.perf_counter()
            result = await self._convert_with_deadline(
                tmp_path, page_break_placeholder, is_disconnected
            )
            markdown, page_count, image_count = result
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.info(
                "parse done: pages=%s images=%d chars=%d elapsed_ms=%s",
                page_count, image_count, len(markdown), elapsed_ms,
            )
            return {
                "markdown": markdown,
                "page_count": page_count,
                "image_count": image_count,
                "elapsed_ms": elapsed_ms,
                "device": self._config.parse_device,
                "docling_version": self._docling_version,
            }

    async def _convert_with_deadline(
        self,
        path: Path,
        placeholder: str,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> tuple[str, int | None, int]:
        """데몬 스레드에서 변환, 데드라인·클라이언트 이탈을 폴링.

        asyncio.to_thread를 안 쓰는 이유와 버려진 워커의 drain 처리는 앱의
        ``src/clients/parser/pdf.py::_docling_with_daemon_timeout``이 원전이다 -
        docling은 내부에 취소 지점이 없어 스레드를 중간에 못 죽이고, 할 수 있는
        것은 결과를 아무도 붙들지 않게 하는 것뿐이다.
        """
        q: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                q.put(("ok", self._convert(path, placeholder)))
            except BaseException as exc:  # noqa: BLE001 — 예외를 호출자 스레드로 넘긴다
                q.put(("err", exc))

        t = threading.Thread(target=worker, daemon=True, name="gpu-parse")
        t.start()

        deadline = time.monotonic() + self._config.parse_timeout_s
        try:
            while True:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        raise ParseTimeout(
                            f"변환이 {self._config.parse_timeout_s:.0f}초를 넘었습니다"
                        )
                    if await is_disconnected():
                        # 클라이언트가 떠났다 - 결과를 받을 사람이 없으니 레인을
                        # 즉시 반납한다(변환 자체는 못 죽인다, 아래 drain 참조).
                        raise ClientGone(str(path))
                    await asyncio.sleep(_POLL_INTERVAL_S)
                    continue
                if kind == "err":
                    assert isinstance(payload, BaseException)
                    raise payload
                assert isinstance(payload, tuple)
                return payload  # type: ignore[return-value]
        finally:
            if t.is_alive():
                self._drain_abandoned(t, q, path)

    def _convert(self, path: Path, placeholder: str) -> tuple[str, int | None, int]:
        result = self._converter.convert(path)
        doc = result.document
        markdown = doc.export_to_markdown(page_break_placeholder=f"\n\n{placeholder}\n\n")
        page_count = len(doc.pages) if getattr(doc, "pages", None) is not None else None
        image_count = len(doc.pictures) if getattr(doc, "pictures", None) is not None else 0
        return markdown, page_count, image_count

    @staticmethod
    def _drain_abandoned(
        t: threading.Thread, q: queue.Queue[tuple[str, object]], path: Path
    ) -> None:
        """버려진 워커의 결과를 버리는 데몬 스레드 — 참조가 영구 보관되는 것을 막는다."""

        def drain() -> None:
            try:
                kind, _payload = q.get()
                logger.info("parse abandoned worker drained: kind=%s path=%s", kind, path)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=drain, daemon=True, name="gpu-parse-drain").start()
