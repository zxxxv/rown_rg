"""web_fetch 마크다운 정리(clean_web_markdown)·미리보기 검증.

2026-08-03 실측: 페이지 상단 GTM 추적 iframe·로고 링크·메뉴가 본문 앞에 붙어
미리보기와 임베딩을 오염시켰다(벤처스퀘어 기사 사례).
"""

from src.workflows.stages import (
    _source_preview,
    clean_web_markdown,
    has_usable_content,
    relevance_excerpt,
)

_SAMPLE = (
    "---\n"
    "canonical: https://www.venturesquare.net/828629\n"
    "meta-description: 협업툴 요약\n"
    "---\n"
    "[https://www.googletagmanager.com/ns.html?id=GTM-WRC2CSR]"
    "(https://www.googletagmanager.com/ns.html?id=GTM-WRC2CSR)\n"
    "[![벤처스퀘어](https://www.venturesquare.net/logo.png)](https://www.venturesquare.net)\n"
    "[홈](/) [뉴스](/news) [행사](/events)\n"
    "\n"
    "# 협업툴 시장, 비대면 시대 그 한계는 어디인가\n"
    "협업툴 시장이 빠르게 성장하고 있다는 본문 문장이다. 시장 규모는 계속 커진다.\n"
)


class TestCleanWebMarkdown:
    def test_boilerplate_removed_substance_kept(self):
        cleaned = clean_web_markdown(_SAMPLE)
        assert "googletagmanager" not in cleaned
        assert "logo.png" not in cleaned
        assert "[홈](/)" not in cleaned
        assert "canonical" not in cleaned  # 머리말도 제거
        assert cleaned.startswith("# 협업툴 시장")
        assert "본문 문장이다" in cleaned

    def test_preview_starts_with_article_not_tracker(self):
        preview = _source_preview(_SAMPLE)
        assert preview is not None
        assert preview.startswith("# 협업툴 시장")
        assert "googletagmanager" not in preview

    def test_all_boilerplate_becomes_empty(self):
        junk = "[![로고](https://ex/logo.png)](https://ex)\n[메뉴](/a) [메뉴2](/b)\nhttps://ex/c\n"
        assert clean_web_markdown(junk) == ""
        assert _source_preview(junk) is None

    def test_inline_link_with_text_kept(self):
        md = "정부는 [보도자료](https://ex/press)에서 정책 방향을 밝혔다.\n"
        cleaned = clean_web_markdown(md)
        assert "보도자료" in cleaned and "정책 방향" in cleaned

    def test_gov_banner_removed(self):
        md = (
            "이 누리집은 대한민국 공식 전자정부 누리집입니다.\n"
            "- **공식 누리집 주소 확인하기** go.kr 주소를 사용하는 누리집은…\n"
            "- **아이콘 또는 HTTPS 확인하기** 웹 브라우저의 자물쇠 아이콘과 주소 앞 https…\n"
            "실제 본문 문장이다.\n"
        )
        cleaned = clean_web_markdown(md)
        assert "전자정부" not in cleaned
        assert "실제 본문 문장이다." == cleaned


class TestSpaMenuPlaceholders:
    """JS 렌더 사이트 메뉴 잔재(polarismarketresearch 사례) — 미리보기가 제목부터 시작."""

    _SPA = (
        "× ×\n"
        "Reports - Loading…\n"
        "Services - Loading…\n"
        "About Us - Loading…\n"
        "Search Result ×\n"
        "Reports Press Blogs ×\n"
        "Remote Workplace Services Market Share, Size, Trends, Industry Analysis Report\n"
    ) + ("The market size was valued at USD 20 billion in 2025. " * 5)

    def test_loading_placeholders_removed(self):
        cleaned = clean_web_markdown(self._SPA)
        assert "Loading…" not in cleaned
        assert "× ×" not in cleaned

    def test_preview_skips_short_menu_lines(self):
        preview = _source_preview(self._SPA)
        assert preview is not None
        assert preview.startswith("Remote Workplace Services Market")


class TestKoreanSiteHeaderUi:
    """정부 누리집 헤더 UI(고용노동부 사례 2) — 화면크기·통합검색·javascript 링크."""

    _HEADER = (
        "- 화면크기 * 작게 * 보통 * 조금 크게 * 크게 * 가장 크게 * 초기화\n"
        "- Language 메뉴 검색\n"
        "## 통합검색\n"
        "검색어를 입력해주세요 검색\n"
        "**인기검색어** **최근검색어** 최근검색어 전체 삭제\n"
        '- [민원](javascript:void(0);) * [민원이용안내](javascript:void(0); "메뉴 열림")\n'
    ) + ("유연근무제 도입 사업장의 생산성 변화를 다룬 실제 본문 문장이다. " * 8)

    def test_header_ui_removed(self):
        cleaned = clean_web_markdown(self._HEADER)
        assert "화면크기" not in cleaned
        assert "인기검색어" not in cleaned
        assert "javascript:void" not in cleaned
        assert "실제 본문 문장이다" in cleaned

    def test_preview_starts_with_article(self):
        preview = _source_preview(self._HEADER)
        assert preview is not None
        assert preview.startswith("유연근무제 도입")


class TestRelevanceExcerpt:
    """절 제목 키워드 주변 발췌 — 자체 스니펫(관련성 근거) 생성."""

    _MD = (
        "서론에 해당하는 도입 문장이다. 배경 설명이 길게 이어진다. "
        "국내 협업툴 시장 규모는 2025년 5천억 원으로 성장했다. "
        "성장 동인은 원격근무 확산이다. 결론 문장이다."
    )

    def test_excerpt_centers_on_keyword_sentence(self):
        out = relevance_excerpt(self._MD, ["국내외 시장 규모 및 구조"])
        assert out is not None
        assert "시장 규모는 2025년" in out
        assert out.startswith("…")  # 문서 중간 발췌 표시

    def test_no_keyword_match_returns_none(self):
        # 폴백은 호출부(_source_preview) 몫 — 함수 자체는 정직하게 None
        assert relevance_excerpt("무관한 내용의 문장이다.", ["밸류체인 생태계"]) is None

    def test_stopword_only_title_returns_none(self):
        assert relevance_excerpt(self._MD, ["및 등 관련"]) is None


class TestHasUsableContent:
    def test_banner_only_page_is_not_usable(self):
        """정부 누리집 배너만 회수된 페이지(고용노동부 매뉴얼 사례) → 본문 없음 판정."""
        md = (
            "이 누리집은 대한민국 공식 전자정부 누리집입니다.\n"
            "- **공식 누리집 주소 확인하기** go.kr …\n"
            "짧은 잔재 문장.\n"
        )
        assert has_usable_content(md) is False

    def test_real_article_is_usable(self):
        assert has_usable_content("의미 있는 본문 문장이다. " * 30) is True

    def test_empty_is_not_usable(self):
        assert has_usable_content("") is False
        assert has_usable_content(None) is False
