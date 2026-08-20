"""원격 docling 변환기 — 전송 형식·429 재시도·쿨다운·응답 검증.

리랭커 테스트와 지키려는 것이 다르다: 여기의 핵심은 **429가 즉시 폴백이 아니라
재시도**라는 점이다. 파싱의 즉시 폴백은 앱 서버 docling — GPU가 바쁠 때 정확히
원래 사고(메모리 폭주) 경로라, 잠깐 기다리는 쪽이 안전하다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from src.clients.parser.base import PAGE_BREAK_MARKER
from src.clients.parser.remote import RemoteDoclingConverter


def _converter(handler, *, cooldown_s: float = 60.0, max_bytes: int = 25 * 1024 * 1024):
    transport = httpx.MockTransport(handler)
    return RemoteDoclingConverter(
        base_url="http://gpu.local:8009",
        token="secret",
        timeout_s=5.0,
        connect_timeout_s=1.0,
        cooldown_s=cooldown_s,
        max_bytes=max_bytes,
        client=httpx.AsyncClient(transport=transport),
    )


def _pdf(tmp_path: Path) -> Path:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def _ok_payload() -> dict:
    return {"markdown": "# Doc", "page_count": 3, "image_count": 1, "elapsed_ms": 10.0}


class TestHappyPath:
    def test_sends_multipart_and_placeholder_and_auth(self, tmp_path: Path):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("Authorization")
            seen["content_type"] = request.headers.get("Content-Type", "")
            seen["body"] = request.read()
            return httpx.Response(200, json=_ok_payload())

        result = asyncio.run(_converter(handler).convert(_pdf(tmp_path)))
        assert result == ("# Doc", 3, 1)
        assert seen["path"] == "/v1/parse"
        assert seen["auth"] == "Bearer secret"
        assert seen["content_type"].startswith("multipart/form-data")
        # 페이지 마커 계약은 앱 소유 - 폼 필드로 그대로 실려 가야 한다.
        assert PAGE_BREAK_MARKER.encode() in seen["body"]
        assert b"application/pdf" in seen["body"]

    def test_empty_markdown_is_valid(self, tmp_path: Path):
        # 이미지 전용 PDF는 정당하게 빈 마크다운일 수 있다 - 거부하면 그 파일이
        # pymupdf로 떨어져 오히려 품질 신호가 사라진다.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"markdown": "", "page_count": 1, "image_count": 4})

        markdown, pages, images = asyncio.run(_converter(handler).convert(_pdf(tmp_path)))
        assert markdown == ""
        assert (pages, images) == (1, 4)


class TestBusyRetry:
    def test_429_retries_then_raises_without_cooldown(self, tmp_path: Path, monkeypatch):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, json={"detail": "busy"}, headers={"Retry-After": "1"})

        async def _no_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr("src.clients.parser.remote.asyncio.sleep", _no_sleep)
        conv = _converter(handler)
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(conv.convert(_pdf(tmp_path)))
        # 첫 시도 + 재시도 2회 = 3회. 그리고 429는 서비스가 죽은 게 아니므로
        # 쿨다운이 걸리면 안 된다 - 다음 문서는 다시 원격을 시도해야 한다.
        assert calls["n"] == 1 + RemoteDoclingConverter._BUSY_RETRIES
        assert conv.available(1024) is True

    def test_429_then_success_recovers(self, tmp_path: Path, monkeypatch):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"detail": "busy"}, headers={"Retry-After": "1"})
            return httpx.Response(200, json=_ok_payload())

        async def _no_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr("src.clients.parser.remote.asyncio.sleep", _no_sleep)
        result = asyncio.run(_converter(handler).convert(_pdf(tmp_path)))
        assert result[0] == "# Doc"
        assert calls["n"] == 2


class TestCooldown:
    def test_server_error_sets_cooldown(self, tmp_path: Path):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={"detail": "boom"})

        conv = _converter(handler)
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(conv.convert(_pdf(tmp_path)))
        # 쿨다운 중에는 available이 False - 캐시 우회 판단(_docling_would_attempt)이
        # 이걸 보고 죽은 GPU 박스 상대의 재파싱 폭풍을 막는다.
        assert conv.available(1024) is False
        snap = conv.stats_snapshot()
        assert snap["in_cooldown"] is True
        assert snap["last_error"] is not None

    def test_connect_error_sets_cooldown(self, tmp_path: Path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        conv = _converter(handler)
        with pytest.raises(httpx.ConnectError):
            asyncio.run(conv.convert(_pdf(tmp_path)))
        assert conv.available(1024) is False


class TestAvailability:
    def test_size_cap(self, tmp_path: Path):
        conv = _converter(lambda r: httpx.Response(200), max_bytes=100)
        assert conv.available(99) is True
        assert conv.available(100) is False

    def test_unconfigured_url_never_available(self):
        conv = RemoteDoclingConverter(
            base_url="", token="", timeout_s=5.0, connect_timeout_s=1.0,
            cooldown_s=60.0, max_bytes=100,
        )
        assert conv.available(1) is False


class TestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            ["not", "a", "dict"],
            {"markdown": 123},
            {"markdown": None},
            {"markdown": "x", "page_count": -1},
            {"markdown": "x", "page_count": "3"},
            {"markdown": "x", "page_count": 1, "image_count": -2},
            {"markdown": "x", "page_count": True},
        ],
    )
    def test_rejects_malformed_payload(self, tmp_path: Path, payload):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        conv = _converter(handler)
        with pytest.raises(ValueError):
            asyncio.run(conv.convert(_pdf(tmp_path)))
        # 형이 다른 응답은 서비스 이상이다 - 쿨다운이 걸려야 한다.
        assert conv.available(1024) is False


class TestStats:
    def test_counters(self, tmp_path: Path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_ok_payload())

        conv = _converter(handler)
        asyncio.run(conv.convert(_pdf(tmp_path)))
        snap = conv.stats_snapshot()
        assert snap["remote_ok_total"] == 1
        assert snap["mode"] == "remote"
        assert snap["in_cooldown"] is False
