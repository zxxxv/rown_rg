"""PDF 변환 사슬(원격 docling → 로컬 docling → pymupdf)과 캐시 우회 판정.

지키려는 것: (1) 사슬이 어느 단계로 끝나든 ``parser_name``과 하강 사유(warnings)가
남는다 - "조용한 폴백"이 없어야 한다. (2) pymupdf 캐시본은 docling이 가능해지면
재파싱된다 - 저품질본이 캐시에 박혀 영원히 재사용되던 구멍(실사고 2층)의 회귀 방지.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import src.clients.parser.remote as remote_module
from src.clients.parser import PdfParser
from src.clients.parser.base import ParseCache
from src.core.config import settings


def _make_pdf(path: Path, pages: int = 2) -> None:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content line")
    doc.save(str(path))
    doc.close()


class _FakeRemote:
    """원격 변환기 대역 — 호출 여부와 응답을 시나리오별로 제어한다."""

    def __init__(self, *, markdown: str = "# Remote", fail: Exception | None = None,
                 max_bytes: int = 25 * 1024 * 1024, in_cooldown: bool = False) -> None:
        self.markdown = markdown
        self.fail = fail
        self.max_bytes = max_bytes
        self.in_cooldown = in_cooldown
        self.calls = 0

    def available(self, size_bytes: int) -> bool:
        return not self.in_cooldown and size_bytes < self.max_bytes

    async def convert(self, path: Path):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self.markdown, 3, 0


def _busy_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://gpu.local:8009/v1/parse")
    response = httpx.Response(429, request=request)
    return httpx.HTTPStatusError("busy", request=request, response=response)


@pytest.fixture
def remote_on(monkeypatch):
    """원격 설정을 켜고 대역을 싱글턴 자리에 꽂는다."""
    monkeypatch.setattr(settings, "parser_remote_url", "http://gpu.local:8009")
    monkeypatch.setattr(settings, "parser_remote_fallback", "local")

    def _install(fake: _FakeRemote) -> _FakeRemote:
        monkeypatch.setattr(remote_module, "_singleton", fake)
        return fake

    yield _install
    remote_module.reset_remote_docling()


def _parser(tmp_path: Path, **kwargs) -> PdfParser:
    # 로컬 docling은 타임아웃 0.001초로 강제 실패시켜 사슬 하강을 자극한다.
    kwargs.setdefault("small_pdf_timeout_s", 0.001)
    return PdfParser(cache=ParseCache(root=tmp_path / "cache"), **kwargs)


class TestChain:
    async def test_remote_success(self, tmp_path: Path, remote_on):
        fake = remote_on(_FakeRemote())
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        result = await _parser(tmp_path).parse(pdf)
        assert fake.calls == 1
        assert result.parser_name == "docling-remote"
        assert "Remote" in result.markdown
        # 성공 사슬에는 하강 경고가 없어야 한다
        assert not any(w.startswith("remote_parse") for w in result.warnings)

    async def test_remote_failure_falls_to_local_then_pymupdf(
        self, tmp_path: Path, remote_on, monkeypatch
    ):
        fake = remote_on(_FakeRemote(fail=RuntimeError("boom")))
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        # 로컬 docling 단계를 결정론적으로 실패시킨다. 실제 타임아웃(0.001초)에
        # 맡기면 docling 미설치 환경에서 ImportError가 타임아웃과 경주해 경고
        # 문자열이 갈린다 - 검증 대상은 사슬 순서지 그 경주가 아니다.
        async def _always_timeout(self, path, timeout_s):
            raise TimeoutError("forced")

        monkeypatch.setattr(PdfParser, "_docling_with_daemon_timeout", _always_timeout)

        result = await _parser(tmp_path).parse(pdf)
        assert fake.calls == 1
        assert result.parser_name == "pymupdf"
        joined = " | ".join(result.warnings)
        assert "remote_parse_failed:RuntimeError" in joined, joined
        assert "fallback_to_pymupdf4llm:timeout" in joined, joined
        assert "fallback_tables_as_plaintext" in joined, joined

    async def test_remote_429_records_busy_not_failure(self, tmp_path: Path, remote_on):
        remote_on(_FakeRemote(fail=_busy_error()))
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        result = await _parser(tmp_path).parse(pdf)
        joined = " | ".join(result.warnings)
        # 429는 "죽음"이 아니라 "밀림" - 구분되는 경고여야 운영에서 원인을 가린다
        assert "remote_parse_busy" in joined, joined
        assert "remote_parse_failed" not in joined, joined

    async def test_remote_disabled_never_calls(self, tmp_path: Path, remote_on):
        fake = remote_on(_FakeRemote())
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        result = await _parser(tmp_path, remote_enabled=False).parse(pdf)
        # pdf_fetch(웹 수집 PDF)의 옵트아웃 경로 - 원격은 절대 호출되지 않는다
        assert fake.calls == 0
        assert result.parser_name == "pymupdf"

    async def test_size_over_remote_cap_skips_remote(self, tmp_path: Path, remote_on):
        fake = remote_on(_FakeRemote(max_bytes=1))  # 모든 파일이 상한 초과
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        result = await _parser(tmp_path).parse(pdf)
        assert fake.calls == 0
        assert result.parser_name == "pymupdf"

    async def test_fallback_policy_pymupdf_skips_local_docling(
        self, tmp_path: Path, monkeypatch
    ):
        # 원격 미설정 + 정책 pymupdf = 앱 서버에서 docling을 아예 돌리지 않는 배포
        monkeypatch.setattr(settings, "parser_remote_url", "")
        monkeypatch.setattr(settings, "parser_remote_fallback", "pymupdf")
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        result = await _parser(tmp_path).parse(pdf)
        assert result.parser_name == "pymupdf"
        # 로컬 docling을 시도조차 안 했으므로 타임아웃 경고가 없어야 한다
        assert not any(w.startswith("fallback_to_pymupdf4llm") for w in result.warnings)


class TestCacheBypass:
    async def test_pymupdf_cache_retried_when_remote_becomes_available(
        self, tmp_path: Path, remote_on, monkeypatch
    ):
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        # 1차: 원격 쿨다운 중 + 로컬 타임아웃 → pymupdf 결과가 캐시에 남는다
        fake = remote_on(_FakeRemote(in_cooldown=True))
        parser = _parser(tmp_path)
        first = await parser.parse(pdf)
        assert first.parser_name == "pymupdf"

        # 2차: 원격이 살아났다 → pymupdf 캐시본을 무시하고 재파싱해야 한다
        fake.in_cooldown = False
        second = await parser.parse(pdf)
        assert second.cached is False
        assert second.parser_name == "docling-remote"
        assert fake.calls == 1

        # 3차: 이제 docling 캐시본이므로 그대로 적중한다
        third = await parser.parse(pdf)
        assert third.cached is True
        assert third.parser_name == "docling-remote"
        assert fake.calls == 1

    async def test_pymupdf_cache_hits_while_remote_in_cooldown(
        self, tmp_path: Path, remote_on
    ):
        pdf = tmp_path / "a.pdf"
        _make_pdf(pdf)

        # 정책 자체를 pymupdf로 좁혀 로컬 docling 자격도 없앤다 - 남는 자격은 원격뿐
        fake = remote_on(_FakeRemote(in_cooldown=True))
        parser = _parser(tmp_path)
        first = await parser.parse(pdf)
        assert first.parser_name == "pymupdf"

        # 쿨다운이 계속이면 재파싱 폭풍 없이 캐시가 적중해야 한다. 단 로컬 docling
        # 자격(정책 local)이 살아 있으면 재시도가 맞으므로, 로컬 자격을 없애고 확인한다.
        original = settings.parser_remote_fallback
        try:
            settings.parser_remote_fallback = "pymupdf"
            second = await parser.parse(pdf)
        finally:
            settings.parser_remote_fallback = original
        assert second.cached is True
        assert fake.calls == 0


class TestCacheVersion:
    def test_v3_key_differs_from_v2(self, tmp_path: Path):
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF")
        key_v3 = ParseCache._key(pdf)
        try:
            ParseCache._VERSION = "v2"
            key_v2 = ParseCache._key(pdf)
        finally:
            ParseCache._VERSION = "v3"
        # 버전이 키에 섞여야 v2 캐시본(파서 정체 미기록)이 자연 무효화된다
        assert key_v3 != key_v2
