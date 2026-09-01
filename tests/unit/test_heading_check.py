"""하위 헤딩 번호 검사 - v5c-2 정독 실측 결함 4종의 회귀."""

from __future__ import annotations

from src.core.types import SectionPlan
from src.services.qa.heading_check import heading_findings


def _plan(ch: int, sec: int, title: str = "대응수준 진단") -> SectionPlan:
    return SectionPlan(chapter_number=ch, section_number=sec, title=title)


class TestHeadingFindings:
    def test_missing_numbers(self):
        """3.3.3만 있고 1·2가 없다 - 결번."""
        body = "도입부.\n\n### 3.3.3 정량 지표\n내용"
        rows = heading_findings([(_plan(3, 3), body)])
        assert any("결번" in r["detail"] for r in rows)

    def test_orphan_single_subsection(self):
        body = "## 4.4.1 국내 적용 동향\n내용뿐"
        rows = heading_findings([(_plan(4, 4, "적용 사례"), body)])
        assert any("고아" in r["detail"] for r in rows)

    def test_ghost_reference(self):
        """본문이 4.3.3을 참조하는데 그 헤딩이 없다."""
        body = "## 4.3.1 개요\n...\n## 4.3.2 진단\n4.3.3의 조달 역량 진단과 연계함"
        rows = heading_findings([(_plan(4, 3), body)])
        assert any("유령" in r["detail"] for r in rows)

    def test_title_reprint(self):
        body = "## 대응수준 진단\n\nㅇ 본문 시작"
        rows = heading_findings([(_plan(3, 3), body)])
        assert any("재출력" in r["detail"] for r in rows)

    def test_clean_section_passes(self):
        body = (
            "ㅇ 도입 문장.\n\n## 2.2.1 법적 기반\n내용\n\n## 2.2.2 작동 원리\n"
            "2.2.1에서 본 기반 위에 전개"
        )
        rows = heading_findings([(_plan(2, 2, "CBAM 상세"), " " + body)])
        assert rows == []

    def test_other_section_numbers_ignored(self):
        """다른 절 번호(3.2절 참조 등)는 유령 참조가 아니다."""
        body = "ㅇ CCA는 3.2.1 논의와 별개로 진행됨"
        rows = heading_findings([(_plan(2, 2, "CBAM 상세"), body)])
        assert rows == []

    def test_all_rows_are_warnings(self):
        body = "## 4.4.1 유일\n4.4.9 참조"
        rows = heading_findings([(_plan(4, 4, "제목"), body)])
        assert rows and all(r["severity"] == "warning" for r in rows)


class TestStripTitleReprint:
    def test_strips_first_title_heading(self):
        from src.services.qa.heading_check import strip_title_reprint

        out = strip_title_reprint("## 대응수준 진단\n\nㅇ 본문", "대응수준 진단")
        assert out.startswith("ㅇ 본문")

    def test_keeps_non_matching_content(self):
        from src.services.qa.heading_check import strip_title_reprint

        body = "ㅇ 바로 본문 시작\n## 2.2.1 기반"
        assert strip_title_reprint(body, "CBAM 상세") == body
