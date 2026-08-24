from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from src.clients.parser import (
    HwpxParser,
    ParseCache,
    ParseResult,
    ParserRegistry,
    PdfParser,
    UnsupportedFormatError,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.hwpx"


class TestParseResult:
    def test_parseresult_serializable(self):
        result = ParseResult(
            source_path=Path("/tmp/foo.hwpx"),
            markdown="# title\n\nbody",
            warnings=["a"],
        )
        dumped = result.model_dump_json()
        parsed = json.loads(dumped)
        assert parsed["markdown"] == "# title\n\nbody"
        assert parsed["warnings"] == ["a"]

        roundtrip = ParseResult.model_validate_json(dumped)
        assert roundtrip.markdown == result.markdown
        assert roundtrip.source_path == result.source_path


class TestParserRegistry:
    def test_registry_routes_hwpx_to_hwpx_parser(self):
        registry = ParserRegistry()
        parser = registry.resolve(Path("doc.hwpx"))
        assert isinstance(parser, HwpxParser)

    def test_registry_routes_pdf_to_pdf_parser(self):
        registry = ParserRegistry()
        parser = registry.resolve(Path("doc.pdf"))
        assert isinstance(parser, PdfParser)

    def test_unsupported_format_raises(self):
        registry = ParserRegistry()
        with pytest.raises(UnsupportedFormatError):
            registry.resolve(Path("doc.xlsx"))  # .docx는 이제 지원 → 진짜 미지원 확장자로


class TestHwpxParser:
    def test_supports_hwpx_extension(self):
        parser = HwpxParser()
        assert parser.supports(Path("a.hwpx"))
        assert parser.supports(Path("a.HWPX"))
        assert not parser.supports(Path("a.pdf"))
        assert not parser.supports(Path("a.hwp"))

    async def test_parse_returns_parseresult(self, tmp_path: Path):
        parser = HwpxParser(cache=ParseCache(root=tmp_path / "cache"))
        result = await parser.parse(FIXTURE)
        assert isinstance(result, ParseResult)
        assert result.source_path == FIXTURE
        assert result.markdown
        assert result.metadata.char_count == len(result.markdown)
        assert result.cached is False

    async def test_cache_hit_on_second_parse(self, tmp_path: Path):
        cache = ParseCache(root=tmp_path / "cache")
        parser = HwpxParser(cache=cache)

        first = await parser.parse(FIXTURE)
        assert first.cached is False

        second = await parser.parse(FIXTURE)
        assert second.cached is True
        assert second.markdown == first.markdown
        assert second.metadata.model_dump() == first.metadata.model_dump()

    async def test_cache_miss_on_file_modification(self, tmp_path: Path):
        cache = ParseCache(root=tmp_path / "cache")
        parser = HwpxParser(cache=cache)

        sample = tmp_path / "sample.hwpx"
        sample.write_bytes(FIXTURE.read_bytes())

        first = await parser.parse(sample)
        assert first.cached is False

        future = sample.stat().st_mtime + 100
        os.utime(sample, (future, future))

        second = await parser.parse(sample)
        assert second.cached is False


def _run(md: str) -> str:
    return HwpxParser._postprocess_markdown(md)


class TestChapterPattern:
    """정규식 #1: ^제\\s*\\d+\\s*장\\s+.+$ → '# '"""

    @pytest.mark.parametrize(
        "line",
        [
            "제1장 서론",
            "제 1 장 본론",
            "제10장 결론",
            "제  3  장  방법",
            "제1234장 메가챕터",
            "제5장\t탭으로 구분",
        ],
    )
    def test_chapter_positive(self, line: str):
        out = _run(line + "\n")
        assert out.startswith("# " + line)

    @pytest.mark.parametrize(
        "line",
        [
            "예제1장 서론",
            "이 책의 제1장은 어렵다",
            "제 장 서론",
            "제1장이다",
            "제a장 가짜",
        ],
    )
    def test_chapter_negative(self, line: str):
        out = _run(line + "\n")
        assert not out.lstrip().startswith("# ")


class TestSectionPattern:
    """정규식 #2: ^제\\s*\\d+\\s*절\\s+.+$ → '## '"""

    @pytest.mark.parametrize(
        "line",
        [
            "제1절 연구 배경",
            "제 2 절 방법",
            "제15절 결과",
            "제3절\t탭",
            "제 100 절 결론",
        ],
    )
    def test_section_positive(self, line: str):
        out = _run(line + "\n")
        assert out.startswith("## " + line)

    @pytest.mark.parametrize(
        "line",
        [
            "제1절은 짧다",
            "제 절 abc",
            "본 제1절 설명에서",
            "제1.5절 중간",
        ],
    )
    def test_section_negative(self, line: str):
        out = _run(line + "\n")
        assert not out.lstrip().startswith("## ")


class TestRomanNumeralPattern:
    """정규식 #3: ^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\\.\\s+.+$ → '# '"""

    @pytest.mark.parametrize(
        "line",
        [
            "Ⅰ. 서론",
            "Ⅱ. 본론",
            "Ⅳ. 방법",
            "Ⅹ. 결론",
            "Ⅲ.  공백두번",
        ],
    )
    def test_roman_positive(self, line: str):
        out = _run(line + "\n")
        assert out.startswith("# " + line)

    @pytest.mark.parametrize(
        "line",
        [
            "I. 라틴알파벳",
            "Ⅰ 점없음",
            "예 Ⅰ. 서론",
            "i. 소문자",
        ],
    )
    def test_roman_negative(self, line: str):
        out = _run(line + "\n")
        assert not out.lstrip().startswith("# ")


class TestNumberedKoreanPattern:
    """정규식 #4: ^\\d+\\.\\s+[가-힣].+$ → '### '"""

    @pytest.mark.parametrize(
        "line",
        [
            "1. 가나다",
            "12. 한국어",
            "1.  시작 (다중공백)",
            "5. 종류와 ABC혼합",
            "100. 마지막",
        ],
    )
    def test_numbered_korean_positive(self, line: str):
        out = _run(line + "\n")
        assert out.startswith("### " + line)

    @pytest.mark.parametrize(
        "line",
        [
            "1. abc 영문시작",
            "a. 가나다",
            "1.가나다",
            "1. 123 숫자시작",
            "1. !특수문자",
        ],
    )
    def test_numbered_korean_negative(self, line: str):
        out = _run(line + "\n")
        assert not out.lstrip().startswith("### ")


class TestKoreanLetterPattern:
    """정규식 #5: ^[가나다라마바사아자차]\\.\\s+.+$ → '#### '"""

    @pytest.mark.parametrize(
        "line",
        [
            "가. 첫째 항목",
            "나. 둘째",
            "마. 다섯째",
            "차. 마지막",
            "사.  다중공백",
        ],
    )
    def test_korean_letter_positive(self, line: str):
        out = _run(line + "\n")
        assert out.startswith("#### " + line)

    @pytest.mark.parametrize(
        "line",
        [
            "타. 범위밖",
            "카. 범위밖",
            "가 점없음",
            "예 가. 항목",
            "가가. 두글자",
        ],
    )
    def test_korean_letter_negative(self, line: str):
        out = _run(line + "\n")
        assert not out.lstrip().startswith("#### ")


class TestHeadingContextGuards:
    def test_table_cell_pattern_not_converted(self):
        md = "| 제1장 서론 | 값 |\n| --- | --- |\n| 가. 항목 | 1만 |\n"
        out = _run(md)
        assert "| 제1장 서론 | 값 |" in out
        assert "| 가. 항목 | 1만 |" in out
        for prefix in ("# 제1장", "#### 가."):
            assert not any(line.lstrip().startswith(prefix) for line in out.split("\n"))

    def test_existing_heading_not_double_prefixed(self):
        md = "# 제1장 서론\n## 제1절 배경\n### 1. 가설\n"
        out = _run(md)
        assert "# 제1장 서론" in out
        assert "## 제1절 배경" in out
        assert "### 1. 가설" in out
        for bad in ("## 제1장", "### 제1절", "#### 1."):
            assert bad not in out

    def test_code_block_pattern_not_converted(self):
        md = "```\n제1장 서론\n가. 코드안항목\n```\n\n제2장 본론\n"
        out = _run(md)
        lines = out.split("\n")
        assert "제1장 서론" in lines
        assert "가. 코드안항목" in lines
        assert "# 제1장 서론" not in lines
        assert "#### 가. 코드안항목" not in lines
        assert "# 제2장 본론" in lines


class TestEmptyTableFilter:
    def test_all_cells_empty_filtered(self):
        md = "|   |   |\n| --- | --- |\n|   |   |\n|   |   |\n"
        out = _run(md)
        assert "|" not in out

    def test_header_only_no_data_filtered(self):
        md = "| 항목 | 값 |\n| --- | --- |\n"
        out = _run(md)
        assert "|" not in out

    def test_total_text_under_5_chars_filtered(self):
        md = "| a |\n| --- |\n| b |\n"
        out = _run(md)
        assert "|" not in out

    def test_meaningful_table_preserved(self):
        md = "| 항목 | 값 |\n| --- | --- |\n| 노인 인구 | 1만명 |\n| 평균 연령 | 73.4세 |\n"
        out = _run(md)
        assert "| 노인 인구 | 1만명 |" in out
        assert "| 평균 연령 | 73.4세 |" in out

    def test_table_without_separator_preserved(self):
        md = "| a | b |\n| c | d |\n"
        out = _run(md)
        assert "| a | b |" in out
        assert "| c | d |" in out


# ===================================================================
# PdfParser
# ===================================================================
# 합성 PDF는 PyMuPDF로 즉석 생성. 한글 폰트 임베드 비용을 피하려고 본문은 영어로 작성하고,
# 한글 처리 검증은 통합(아래 TestPdfParserIntegration)에서 실제 PDF로 본다.

REAL_PDF_DIR = Path(__file__).resolve().parents[1].parent / "test_data" / "pdf"
# docling은 12MB급 PDF에서 60s 타임아웃 이후에도 worker thread가 살아남아
# pytest event loop teardown을 막는다(명세상 운영은 직렬 처리). 단위 테스트에서는
# 5MB 이상이면 자동 skip하고, 실제 정부 PDF 검증은 scripts/verify_pdf_parser.py로 분리.
_SMALL_PDF_LIMIT_BYTES = 5 * 1024 * 1024
_REAL_PDFS = (
    [p for p in sorted(REAL_PDF_DIR.glob("*.pdf")) if p.stat().st_size <= _SMALL_PDF_LIMIT_BYTES]
    if REAL_PDF_DIR.exists()
    else []
)


def _make_pdf(
    path: Path, *, pages: int = 2, with_page_numbers: bool = True, lines_per_page: int = 6
) -> None:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Chapter {i + 1}", fontname="helv", fontsize=18)
        for j in range(lines_per_page):
            page.insert_text(
                (72, 110 + j * 16),
                f"Page {i + 1} line {j + 1}: a body sentence to make extraction non-empty.",
                fontsize=11,
            )
        if with_page_numbers:
            page.insert_text((300, 750), str(i + 1), fontsize=10)
    doc.save(path)
    doc.close()


class TestPdfParserSupports:
    def test_supports_pdf_extension(self):
        from src.clients.parser import PdfParser

        parser = PdfParser()
        assert parser.supports(Path("a.pdf"))
        assert parser.supports(Path("a.PDF"))
        assert not parser.supports(Path("a.hwpx"))
        assert not parser.supports(Path("a.docx"))

    def test_nonexistent_file_raises(self, tmp_path: Path):
        from src.clients.parser import PdfParser

        async def _run():
            await PdfParser(cache=ParseCache(root=tmp_path / "cache")).parse(
                tmp_path / "missing.pdf"
            )

        with pytest.raises(FileNotFoundError):
            asyncio.run(_run())


class TestPdfParserFallback:
    """docling 타임아웃을 강제로 0초로 만들어 폴백 경로만 단독 검증."""

    def _make_force_fallback_parser(self, tmp_path: Path):
        from src.clients.parser import PdfParser

        return PdfParser(
            cache=ParseCache(root=tmp_path / "cache"),
            # 합성 PDF는 small bucket(<1MB)이라 small_pdf_timeout_s만 0.001로 강제 → 즉시 timeout
            small_pdf_timeout_s=0.001,
            # 개발 환경 .env에 원격 파서(GPU 박스)가 살아 있으면 원격이 성공해 폴백
            # 경로가 아예 안 탄다(2026-08-24 실사고: 박스 복구 직후 이 테스트만 깨짐).
            remote_enabled=False,
        )

    async def test_fallback_emits_timeout_warning(self, tmp_path: Path):
        pdf = tmp_path / "tiny.pdf"
        _make_pdf(pdf, pages=2)
        parser = self._make_force_fallback_parser(tmp_path)

        result = await parser.parse(pdf)
        joined = " | ".join(result.warnings)
        assert "fallback_to_pymupdf4llm:timeout" in joined, joined
        # 폴백 시 표 정확도가 떨어진다는 점도 같이 기록되어야 함
        assert "fallback_tables_as_plaintext" in joined, joined

    async def test_fallback_produces_markdown_and_pages(self, tmp_path: Path):
        pdf = tmp_path / "tiny.pdf"
        _make_pdf(pdf, pages=3)
        parser = self._make_force_fallback_parser(tmp_path)

        result = await parser.parse(pdf)
        assert result.metadata.page_count == 3
        assert result.metadata.char_count > 0
        # 합성 PDF에 이미지 없음 — image_count 기본값 그대로
        assert result.metadata.image_count == 0

    async def test_page_numbers_stripped_from_markdown(self, tmp_path: Path):
        pdf = tmp_path / "with_pgnums.pdf"
        _make_pdf(pdf, pages=4, with_page_numbers=True)
        parser = self._make_force_fallback_parser(tmp_path)

        result = await parser.parse(pdf)
        # `_strip_page_numbers`는 단독으로 "1", "2" 등으로 된 줄을 제거.
        # 본문에 "Page N line ..." 같은 문장은 남기지만 외로운 페이지 번호 줄은 사라져야 한다.
        for n in (1, 2, 3, 4):
            lonely_line = f"\n{n}\n"
            assert lonely_line not in result.markdown, f"단독 페이지 번호 '{n}' 남음"

    async def test_cache_hit_on_second_parse(self, tmp_path: Path):
        """pymupdf 캐시본은 docling을 다시 시도할 수 없는 조건에서만 적중한다.

        v3부터 pymupdf 결과는 "그때 docling이 안 됐다"의 기록이라, 지금 docling이
        가능하면 캐시를 무시하고 재파싱한다. 여기서는 large 버킷(medium 상한 0)으로
        docling 자격 자체를 없애 캐시 적중을 확인한다 - 재시도 동작 자체는
        test_pdf_parser_remote.py의 캐시 판정 테스트가 검증한다.
        """
        from src.clients.parser import PdfParser

        pdf = tmp_path / "cache.pdf"
        _make_pdf(pdf, pages=2)
        parser = PdfParser(
            cache=ParseCache(root=tmp_path / "cache"),
            medium_pdf_max_bytes=0,
            # 원격 파서가 살아 있으면 large 버킷도 원격이 받아 캐시 시나리오가 깨진다.
            remote_enabled=False,
        )

        first = await parser.parse(pdf)
        assert first.cached is False
        assert first.parser_name == "pymupdf"
        second = await parser.parse(pdf)
        assert second.cached is True
        # 캐시 적중 시 메타데이터가 동일해야 함
        assert second.metadata.model_dump() == first.metadata.model_dump()


class TestPdfParserSizeBuckets:
    """크기 분기 정책 — 5MB+는 docling 시도 안 함, 1MB·5MB 임계의 의미 검증."""

    async def test_large_bucket_skips_docling(self, tmp_path: Path):
        from src.clients.parser import PdfParser

        pdf = tmp_path / "tiny_but_large_bucket.pdf"
        _make_pdf(pdf, pages=2)
        # 임계 0이면 합성 PDF도 large bucket → docling 시도 안 함
        parser = PdfParser(
            cache=ParseCache(root=tmp_path / "cache"),
            medium_pdf_max_bytes=0,
            # 원격 파서가 살아 있으면 large 버킷도 원격이 받아 skip 시그널이 안 나온다.
            remote_enabled=False,
        )
        result = await parser.parse(pdf)
        joined = " | ".join(result.warnings)
        # 5MB+ skip 시그널은 timeout fallback과 구별되어야 함
        assert "skip_docling_too_large" in joined, joined
        assert "fallback_to_pymupdf4llm:timeout" not in joined, joined
        # pymupdf4llm 직접 경로라 평문 표 warning은 여전히 붙음
        assert "fallback_tables_as_plaintext" in joined, joined


class TestPdfParserDoesNotHangAfterTimeout:
    """timeout 이후 main caller가 docling worker join을 기다리며 멈추면 안 됨.

    daemon thread + queue polling 패턴이 깨지면(예: asyncio.to_thread로 회귀)
    이 테스트가 5s+의 hang으로 잡힌다.
    """

    async def test_main_caller_proceeds_immediately_after_timeout(
        self, tmp_path: Path, monkeypatch
    ):
        import time as _time

        import src.clients.parser.pdf as pdf_module
        from src.clients.parser import PdfParser

        # docling을 "5초 동안 자고 결과 반환"으로 monkeypatch.
        # main이 worker join을 기다리면 5초+, daemon 패턴이면 timeout 직후 진행.
        def slow_docling(p):
            _time.sleep(5.0)
            return ("never seen", None, 0)

        monkeypatch.setattr(pdf_module, "_docling_convert", slow_docling)

        pdf = tmp_path / "x.pdf"
        _make_pdf(pdf, pages=1)
        parser = PdfParser(
            cache=ParseCache(root=tmp_path / "cache"),
            small_pdf_timeout_s=0.2,
            # 원격 파서가 살아 있으면 monkeypatch한 로컬 docling을 아예 안 탄다.
            remote_enabled=False,
        )

        t0 = _time.perf_counter()
        result = await parser.parse(pdf)
        elapsed = _time.perf_counter() - t0
        # 0.2s timeout + pymupdf4llm 폴백(<1s on tiny synthetic) → 2초 안에는 끝나야 함.
        assert elapsed < 2.0, f"main hang: {elapsed:.2f}s (worker join을 기다린 것)"
        assert any(w.startswith("fallback_to_pymupdf4llm:timeout") for w in result.warnings), (
            result.warnings
        )


@pytest.mark.skipif(
    not _REAL_PDFS,
    reason="test_data/pdf/에 ≤5MB PDF가 없음 (큰 PDF는 verify_pdf_parser.py로 수동 검증)",
)
class TestPdfParserIntegration:
    """실제 5MB 이하 PDF로 docling 정상 경로 검증. 큰 정부 PDF는 별도 manual 스크립트."""

    async def test_real_pdf_produces_nonempty_markdown(self, tmp_path: Path):
        from src.clients.parser import PdfParser

        pdf = _REAL_PDFS[0]
        parser = PdfParser(cache=ParseCache(root=tmp_path / "cache"))
        result = await parser.parse(pdf)
        assert result.metadata.char_count > 100
        assert result.metadata.page_count is not None
        assert result.metadata.page_count >= 1


class TestAbandonedDoclingWorker:
    """타임아웃으로 버려진 docling 워커가 메모리를 붙들지 않는지.

    2026-08-12: 서버가 완성된 런 이후에도 anon 29.3GB를 유지했다. 코드를 보니
    타임아웃된 워커가 `_active_docling_workers`에서 안 빠지고(`not t.is_alive()`
    조건), 결과 큐도 아무도 안 읽어 완성된 마크다운이 프로세스 내내 남았다.
    "프로세스 종료 때 정리된다"는 전제가 서버에서는 성립하지 않는다.
    """

    @pytest.mark.asyncio
    async def test_타임아웃된_워커가_추적목록에_안_남는다(self, monkeypatch) -> None:
        import time as _time

        from src.clients.parser import pdf as pdfmod

        def slow(_path):
            _time.sleep(0.5)
            return ("결과", 1, 0)

        monkeypatch.setattr(pdfmod, "_docling_convert", slow)
        parser = pdfmod.PdfParser()

        for _ in range(3):
            with pytest.raises(TimeoutError):
                await parser._docling_with_daemon_timeout(Path("pyproject.toml"), 0.05)

        assert pdfmod._active_docling_workers == [], "타임아웃 워커가 목록에 남았다"

    @pytest.mark.asyncio
    async def test_버려진_워커의_결과가_큐에_쌓이지_않는다(self, monkeypatch) -> None:
        """큐가 결과를 붙들면 완성된 마크다운이 통째로 남는다."""
        import time as _time

        from src.clients.parser import pdf as pdfmod

        payload = "가" * 100_000

        def slow(_path):
            _time.sleep(0.3)
            return (payload, 1, 0)

        monkeypatch.setattr(pdfmod, "_docling_convert", slow)
        parser = pdfmod.PdfParser()

        before = {t.ident for t in threading.enumerate() if t.name == "pdf-docling-drain"}
        with pytest.raises(TimeoutError):
            await parser._docling_with_daemon_timeout(Path("pyproject.toml"), 0.05)

        # 이 호출이 띄운 정리 스레드만 본다 - 다른 테스트의 잔여 스레드를 세면 안 된다.
        mine = [
            t
            for t in threading.enumerate()
            if t.name == "pdf-docling-drain" and t.ident not in before
        ]
        assert mine, "버려진 워커에 정리 스레드가 안 붙었다"
        for t in mine:
            t.join(timeout=3)
            assert not t.is_alive(), "정리 스레드가 안 끝났다 - 큐가 비워지지 않았다"
