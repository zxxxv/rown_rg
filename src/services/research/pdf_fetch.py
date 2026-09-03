"""PDF 출처를 우리가 직접 회수·파싱한다 — 서버 web_fetch의 100페이지 상한 우회.

배경: Anthropic web_fetch가 회수한 PDF는 서버 내부 대화에 누적되고, 요청당 100페이지를
넘기면 400으로 거부돼 **챕터 수집이 통째로 죽는다**(2026-08-06/07 실측: 12콜 중 6콜
전멸, 자료 18건→6건). `max_content_tokens`는 "does not apply to binary content such as
PDFs"(SDK 타입 주석)라 제동 장치가 없고, 실패가 서버 안에서 나므로 클라이언트가 개입할
지점도 없다.

그래서 PDF만 회수 주체를 우리로 옮긴다. HTML은 그대로 API가 회수하므로 HTML→마크다운
변환기(새 의존성)가 필요 없다 — 범위를 PDF로 좁힌 이유다.

웹 수집 PDF는 **pymupdf 직행**(docling 우회)이다. docling은 1~13MB 구간에서 600초까지
쓰고 메모리 스파이크가 크다(로컬 실측 임베딩만으로 이미 97%). 표 충실도를 잃지만 수집
자료는 청킹→임베딩→검색 근거로만 쓰이므로 텍스트 흐름이면 충분하다. 사용자가 직접
올린 핵심 문서는 기존 업로드 경로에서 docling을 그대로 쓴다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

from src.clients.parser.pdf import PdfParser

logger = structlog.get_logger(__name__)

# 동시 다운로드 수 — 로컬 파싱이 CPU·메모리를 쓰므로 낮게 잡는다.
DEFAULT_CONCURRENCY = 3
DEFAULT_TIMEOUT_S = 60.0
# SSRF 방어 — 리다이렉트를 직접 따라가며 매 홉의 대상을 검증한다(최대 홉 수).
MAX_REDIRECTS = 5
# 내려받을 최대 크기 — 이보다 크면 파싱 시간이 수집 전체를 잡아먹는다.
MAX_PDF_BYTES = 40 * 1024 * 1024
# 스캔 PDF 판정 — 페이지당 이 글자 수 미만이면 이미지 전용으로 보고 버린다.
# (docling OCR이 꺼져 있어 스캔 문서는 빈 텍스트가 나온다)
MIN_CHARS_PER_PAGE = 50

# 크롤러 신원 표기 - 연락처 도메인은 운영 도메인과 일치해야 한다(옛 브랜드
# loweninsight.kr로 남아 있던 것, 2026-09-03 감사).
_UA = "Mozilla/5.0 (compatible; RownReportBot/1.0; +https://www.rowninsight.cloud)"


def looks_like_pdf(url: str, content_type: str | None = None) -> bool:
    """URL 확장자 또는 Content-Type으로 PDF 판정."""
    if content_type and "pdf" in content_type.lower():
        return True
    path = (urlparse(url).path or "").lower()
    return path.endswith(".pdf")


def _is_scanned(markdown: str, page_count: int | None) -> bool:
    """텍스트가 페이지 수에 비해 극히 적으면 스캔 PDF로 본다."""
    if not page_count or page_count <= 0:
        return False
    return len(markdown) / page_count < MIN_CHARS_PER_PAGE


class _BlockedURLError(Exception):
    """공인 대상이 아니어서 회수를 거부한 URL (SSRF 방어)."""


_ALLOWED_SCHEMES = {"http", "https"}


async def _assert_public_url(url: str) -> None:
    """URL의 스킴·호스트를 검증하고, 해석된 IP가 하나라도 비공인 대역이면 거부한다.

    회수 대상 URL은 LLM의 web_search 결과에서 오므로(프롬프트 인젝션·주제로 유도 가능)
    사설·루프백·링크로컬(169.254.0.0/16 = 클라우드 메타데이터)·예약·멀티캐스트로의
    요청을 막는다. DNS는 논블로킹으로 해석하고, 리다이렉트는 호출부가 홉마다 재검증한다.

    주: 해석 후 httpx가 연결 시점에 재해석하므로 DNS 리바인딩까지는 막지 못한다 —
    LLM 유도 URL·오픈 리다이렉트라는 실제 위협에는 충분한 완화다.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise _BlockedURLError(f"scheme not allowed: {scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise _BlockedURLError("no host in url")
    port = parsed.port or (443 if scheme == "https" else 80)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise _BlockedURLError(f"dns resolution failed: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise _BlockedURLError(f"non-public address {ip} for host {host}")


class PdfSourceFetcher:
    """PDF URL을 내려받아 마크다운 본문으로 바꾼다. 실패는 URL 단위로 삼킨다."""

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_bytes: int = MAX_PDF_BYTES,
    ) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout = timeout_s
        self._max_bytes = max_bytes
        # medium 상한 0 = 전부 large 취급 → docling 건너뛰고 pymupdf 직행.
        # remote_enabled=False: 웹에서 수집한 신뢰할 수 없는 PDF를 GPU 박스로
        # 보내지 않는다 - 이 옵트아웃이 없으면 원격 상한(25MB) 미만 파일이
        # 조용히 원격 경로를 타기 시작한다.
        self._parser = PdfParser(medium_pdf_max_bytes=0, remote_enabled=False)

    async def fetch_many(self, urls: list[str]) -> dict[str, str]:
        """URL → 본문 마크다운. 회수·파싱에 성공한 것만 담아 돌려준다."""
        if not urls:
            return {}
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,  # 리다이렉트를 직접 따라가며 홉마다 SSRF 검증
            headers={"User-Agent": _UA},
        ) as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, url) for url in urls),
                return_exceptions=True,
            )
        out: dict[str, str] = {}
        for url, result in zip(urls, results, strict=True):
            if isinstance(result, str) and result:
                out[url] = result
        logger.info("pdf_fetch.batch_done", requested=len(urls), fetched=len(out))
        return out

    async def _fetch_one(self, client: httpx.AsyncClient, url: str) -> str | None:
        async with self._sem:
            try:
                response = await self._get_validated(client, url)
                response.raise_for_status()
            except _BlockedURLError as exc:
                logger.warning("pdf_fetch.blocked_url", url=url, reason=str(exc))
                return None
            except Exception as exc:
                logger.warning("pdf_fetch.download_failed", url=url, error=str(exc))
                return None

            body = response.content
            if len(body) > self._max_bytes:
                logger.warning("pdf_fetch.too_large", url=url, size_bytes=len(body))
                return None
            if not looks_like_pdf(str(response.url), response.headers.get("content-type")):
                # 확장자로는 PDF였는데 실제로는 HTML 오류 페이지인 경우 등.
                logger.warning("pdf_fetch.not_pdf", url=url)
                return None

            return await self._parse(url, body)

    async def _get_validated(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        """리다이렉트를 직접 따라가되 매 홉의 대상을 SSRF 검증한다.

        client는 follow_redirects=False여야 한다 — httpx가 내부/메타데이터 호스트로
        몰래 점프하는 것을 막고, 각 Location을 우리가 검증한 뒤에만 따라간다.
        """
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await _assert_public_url(current)
            response = await client.get(current)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return response
                current = str(response.url.join(location))
                continue
            return response
        raise _BlockedURLError(f"too many redirects (>{MAX_REDIRECTS})")

    async def _parse(self, url: str, body: bytes) -> str | None:
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(body)
                tmp_path = Path(tmp.name)
            result = await self._parser.parse(tmp_path)
        except Exception as exc:
            logger.warning("pdf_fetch.parse_failed", url=url, error=str(exc))
            return None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        markdown = result.markdown or ""
        if _is_scanned(markdown, result.metadata.page_count):
            # OCR이 꺼져 있어 스캔 문서는 본문이 사실상 비어 나온다 — 풀에 넣지 않는다.
            logger.warning(
                "pdf_fetch.looks_scanned",
                url=url,
                pages=result.metadata.page_count,
                chars=len(markdown),
            )
            return None
        logger.info(
            "pdf_fetch.parsed",
            url=url,
            pages=result.metadata.page_count,
            chars=len(markdown),
        )
        return markdown
