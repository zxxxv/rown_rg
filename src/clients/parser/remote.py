"""원격 docling 변환기 — PDF를 GPU 박스로 보내 파싱한다.

``RemoteRerankerClient``를 본떴지만 **429의 의미가 다르다**. 리랭커는 폴백(로컬
재채점)이 싸서 429를 받으면 즉시 폴백하는 것이 옳다. 파싱의 즉시 폴백은 앱
서버에서 docling을 돌리는 것 — GPU가 바쁠 때 정확히 원래 사고(메모리 폭주 →
pymupdf 조용한 폴백) 경로다. 파싱은 배경 작업이라 지연을 감수할 수 있으므로
429는 Retry-After를 존중해 잠깐 기다렸다 재시도하고, 소진된 뒤에야 사슬을
내려간다. 429는 쿨다운을 걸지 않는다(서비스가 죽은 게 아니라 밀린 것).

폴백 자체는 이 클라이언트가 하지 않는다 — 실패를 올리면 ``PdfParser._convert``의
사슬(원격 → 로컬 docling → pymupdf)이 다음 단계를 정한다. 어느 단계로 끝났는지는
``parser_name``으로 기록되어 화면까지 간다.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
import structlog

from src.clients.parser.base import PAGE_BREAK_MARKER
from src.clients.remote_stats import RemoteCallStats
from src.core.config import settings

logger = structlog.get_logger(__name__)

# 전송층 순단으로 즉시 실패하는 4종만 1회 재시도한다 — 3원격 동일 벨트.
# 타임아웃 계열(httpx.TimeoutException·asyncio.TimeoutError)은 일부러 뺐다:
# 이미 상한 수십 초를 다 쓴 실패라 재시도가 지연을 2배로 만든다. httpx 계층상
# TimeoutException은 이 4종의 조상도 자손도 아니라 튜플 매칭만으로 배제된다.
# HTTPStatusError는 재시도하지 않는다 — 429는 _request의 기존 대기 루프가 처리한다.
_TRANSPORT_RETRYABLE = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
)


class RemoteDoclingConverter:
    """HTTP로 GPU 추론 서비스의 /v1/parse를 부른다. 폴백 없음 — 실패는 올린다."""

    # 429 재시도 횟수·대기 상한. 설정으로 빼지 않는 이유: 조정할 근거가 생길 만큼
    # 자주 바뀌는 값이 아니고, 설정 표면을 늘리면 안 읽는 항목만 는다.
    _BUSY_RETRIES: ClassVar[int] = 2
    _BUSY_WAIT_CAP_S: ClassVar[float] = 10.0

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_s: float | None = None,
        connect_timeout_s: float | None = None,
        cooldown_s: float | None = None,
        max_bytes: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # None=설정 폴백, ""=명시적 미구성. `or` 폴백은 둘을 구분 못 해 .env가 설정된
        # 환경에서 "미구성 컨버터"를 만들 수 없었다(2026-08-20, 테스트가 드러냄).
        self._base_url = (settings.parser_remote_url if base_url is None else base_url).rstrip("/")
        self._token = token if token is not None else settings.parser_remote_token
        self._timeout_s = timeout_s or settings.parser_remote_timeout_s
        self._connect_timeout_s = connect_timeout_s or settings.parser_remote_connect_timeout_s
        self._cooldown_s = (
            cooldown_s if cooldown_s is not None else settings.parser_remote_cooldown_s
        )
        self._max_bytes = max_bytes or settings.parser_remote_max_bytes
        self._client = client
        self._disabled_until: float = 0.0
        self.stats = RemoteCallStats()

        if self._base_url:
            logger.info(
                "parser.remote.configured",
                base_url=self._base_url,
                max_bytes=self._max_bytes,
                timeout_s=self._timeout_s,
                has_token=bool(self._token),
            )

    def available(self, size_bytes: int) -> bool:
        """이 파일을 지금 원격으로 보낼 수 있는가 — 설정·쿨다운·크기를 한 번에.

        캐시 우회 판단(``PdfParser._docling_would_attempt``)도 이걸 쓴다. 쿨다운을
        여기 포함하는 것이 중요하다: 죽은 GPU 박스를 상대로 pymupdf 캐시를 계속
        무시하고 재파싱을 반복하는 폭풍을 막는다.
        """
        return bool(self._base_url) and not self._in_cooldown() and size_bytes < self._max_bytes

    async def convert(self, path: Path) -> tuple[str, int | None, int]:
        """PDF 1건을 (markdown, page_count, image_count)로. 실패는 예외로 올린다."""
        t0 = time.perf_counter()
        try:
            payload = await self._request(path)
        except Exception as exc:  # noqa: BLE001 — 사슬이 다음 단계를 정하도록 올린다
            busy = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            if not busy:
                self._disabled_until = time.monotonic() + self._cooldown_s
            self.stats.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning(
                "parser.remote.failed",
                error=type(exc).__name__,
                detail=str(exc)[:200],
                path=str(path),
                busy=busy,
                cooldown_s=0 if busy else self._cooldown_s,
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            raise

        self.stats.record_ok()
        logger.info(
            "parser.remote.completed",
            path=str(path),
            page_count=payload[1],
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return payload

    async def _request(self, path: Path) -> tuple[str, int | None, int]:
        """multipart 전송 + 429 재시도 루프 + 응답 검증."""
        client = self._ensure_client()
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}

        attempt = 0
        while True:
            try:
                response = await self._post_file(client, path, headers)
            except _TRANSPORT_RETRYABLE as exc:
                # 전송층 순단 1회 재시도 벨트(3원격 동일): 순단 1건이 쿨다운을
                # 열면 그 60초 창의 파일 전부가 원격 시도 없이 사슬 아래(앱 서버
                # docling)로 내려간다 - 정확히 원래 사고(메모리 폭주) 경로다
                # (2026-08-24 임베딩 경로 실사고: RemoteProtocolError 1건, 7.4ms
                # 즉시 실패 → 폴백 124건 증폭). 순단은 즉시 실패라 재시도가 싸고,
                # 재시도도 실패하면 그대로 올려 기존 쿨다운+사슬 폴백을 탄다.
                self.stats.record_transport_retry()
                logger.warning(
                    "parser.remote.transport_retry",
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                    path=str(path),
                )
                response = await self._post_file(client, path, headers)
            if response.status_code == 429 and attempt < self._BUSY_RETRIES:
                attempt += 1
                retry_after = self._retry_after_s(response)
                logger.info(
                    "parser.remote.busy_retry",
                    attempt=attempt,
                    wait_s=retry_after,
                    path=str(path),
                )
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            return self._validate(response.json())

    async def _post_file(
        self, client: httpx.AsyncClient, path: Path, headers: dict[str, str]
    ) -> httpx.Response:
        # 시도마다 파일을 새로 연다 - 실패한 전송이 핸들 위치를 어디까지 소모했는지
        # 알 수 없어, 같은 핸들 재전송은 빈/잘린 본문이 된다. seek(0)보다 재오픈이
        # 확실하고, 429 대기 루프도 원래 매 시도 새로 열던 구조라 결이 같다.
        with path.open("rb") as fh:
            # 코루틴 차원 상한 - 터널이 끊은 소켓의 대기가 깨어나지 못한 채
            # 영구 정지한 실사례(2026-08-21 v6, 임베딩 경로) 대응. 3원격 동일 벨트.
            # 이 상한은 순단 재시도와 무관하게 시도마다 각각 적용된다.
            return await asyncio.wait_for(
                client.post(
                    f"{self._base_url}/v1/parse",
                    files={"file": (path.name, fh, "application/pdf")},
                    # 페이지 마커 계약은 앱 소유다 - 서버는 이 문자열을 그대로
                    # export_to_markdown에 넣는다. 상수를 서버에 복제하면 언젠가
                    # 어긋나 페이지 점프가 조용히 깨진다.
                    data={"page_break_placeholder": PAGE_BREAK_MARKER},
                    headers=headers,
                ),
                timeout=self._timeout_s + 60,
            )

    def _retry_after_s(self, response: httpx.Response) -> float:
        try:
            wait = float(response.headers.get("Retry-After", "5"))
        except ValueError:
            wait = 5.0
        return min(max(wait, 1.0), self._BUSY_WAIT_CAP_S)

    @staticmethod
    def _validate(payload: Any) -> tuple[str, int | None, int]:
        """응답을 믿기 전에 형을 확인한다.

        markdown 빈 문자열은 허용한다 - 이미지 전용 PDF는 정당하게 비어 있을 수
        있고, 여기서 거부하면 그 파일이 pymupdf로 떨어져 오히려 품질 신호(경고)가
        사라진다. 형이 아예 다른 것만 실패로 취급한다.
        """
        if not isinstance(payload, dict):
            raise ValueError(f"응답이 객체가 아님: {type(payload).__name__}")
        markdown = payload.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError(f"markdown이 문자열이 아님: {type(markdown).__name__}")
        page_count = payload.get("page_count")
        if page_count is not None and (
            isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 0
        ):
            raise ValueError(f"page_count가 이상함: {page_count!r}")
        image_count = payload.get("image_count", 0)
        if isinstance(image_count, bool) or not isinstance(image_count, int) or image_count < 0:
            raise ValueError(f"image_count가 이상함: {image_count!r}")
        return markdown, page_count, image_count

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self._timeout_s,
                    connect=self._connect_timeout_s,
                    # 터널 너머로 25MB를 올리는 시간. read와 분리해 두는 이유:
                    # read는 변환 대기(수 분)라 업로드 지연과 성격이 다르다.
                    write=120.0,
                    pool=self._connect_timeout_s,
                ),
            )
        return self._client

    def _in_cooldown(self) -> bool:
        return time.monotonic() < self._disabled_until

    def stats_snapshot(self) -> dict[str, Any]:
        """모니터 라우터 노출용 — 리랭커·임베딩 클라이언트와 같은 모양."""
        return {
            "mode": "remote",
            "fallback_policy": settings.parser_remote_fallback,
            "base_url": self._base_url,
            **self.stats.snapshot(disabled_until_monotonic=self._disabled_until),
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ParserRegistry가 PdfParser를 무인자 `cls()`로 만들므로(registry.py), 원격 변환기는
# 모듈 싱글턴으로 공유한다 - 쿨다운·카운터·httpx 커넥션이 파서 인스턴스마다
# 갈라지면 쿨다운이 무력화된다. reranker_factory와 같은 패턴.
_singleton: RemoteDoclingConverter | None = None


def get_remote_docling() -> RemoteDoclingConverter:
    global _singleton
    if _singleton is None:
        _singleton = RemoteDoclingConverter()
    return _singleton


def peek_remote_docling() -> RemoteDoclingConverter | None:
    """이미 만들어진 싱글턴만 — 모니터 조회가 상태를 바꾸지 않게 한다."""
    return _singleton


def reset_remote_docling() -> None:
    """싱글턴 해제 (테스트·재설정용)."""
    global _singleton
    _singleton = None
