"""목차 지시 이행 검사 — 항목 추출과 미반영 판정(순수 로직, LLM·DB 없음)."""

from __future__ import annotations

from src.services.qa.design_coverage import (
    coverage_terms,
    covered,
    findings_for_section,
)


class TestCoverageTerms:
    def test_key_points_are_the_checklist(self):
        # 실제 목차(예타 1.1): key_point 하나가 곧 검사 항목이다.
        got = coverage_terms(
            "국가 정책 흐름에서 본 사업 필요성 및 글로벌 동향",
            ["국정과제 연계", "글로벌 경쟁 현황", "사업 추진 시급성"],
        )
        assert got == ["국정과제 연계", "글로벌 경쟁 현황", "사업 추진 시급성"]

    def test_nested_list_is_split(self):
        # "6.2.2 위험요인 - 인허가·환경·법령 리스크 식별" → 라벨을 떼고 나열을 쪼갠다.
        got = coverage_terms("", ["6.2.2 위험요인 - 인허가·환경·주민수용성·법령 리스크 식별"])
        assert "인허가" in got and "주민수용성" in got
        assert "위험요인" not in got  # 라벨은 항목이 아니다

    def test_direction_parenthetical_list_counts(self):
        got = coverage_terms(
            "위험요인 및 대응방안(부지 확보·인허가 지연·환경 규제·주민 수용성 등)을 서술", []
        )
        assert "부지 확보" in got and "주민 수용성" in got

    def test_plain_parenthesis_is_not_a_list(self):
        # 나열이 아닌 괄호(부연)는 항목으로 보지 않는다 - 안 그러면 경고가 폭증한다.
        assert coverage_terms("정책 일관성(AHP 기준)을 평가", []) == []

    def test_generic_words_dropped(self):
        assert coverage_terms("", ["분석", "검토", "및"]) == []

    def test_truncated_parenthesis_trimmed(self):
        # "교차전략(SO/ST" 처럼 한쪽만 남은 괄호는 앞부분만 쓴다.
        got = coverage_terms("", ["교차전략(SO/ST"])
        assert got == ["교차전략"]


class TestCovered:
    def test_exact_and_spacing_variants(self):
        assert covered("주민 수용성", "지역 주민수용성 문제가 제기된다")
        assert covered("인허가", "인허가 절차가 지연되고 있다")

    def test_unrelated_text_is_not_covered(self):
        assert not covered("주민 수용성", "반도체 장비 수출액이 증가했다")

    def test_empty_inputs(self):
        assert not covered("", "본문")
        assert not covered("항목", "")


class TestFindings:
    DIRECTION = ""
    POINTS = ["국정과제 연계", "글로벌 경쟁 현황", "사업 추진 시급성"]

    def test_all_covered_is_silent(self):
        content = "국정과제 연계를 검토했고, 글로벌 경쟁 현황과 사업 추진 시급성을 함께 다뤘다."
        assert findings_for_section(1, 1, content, self.DIRECTION, self.POINTS, "") == []

    def test_missing_with_evidence_recommends_rewrite(self):
        content = "국정과제 연계만 서술한다."
        pool = "글로벌 경쟁 현황 자료와 사업 추진 시급성 근거가 담긴 청크"
        rows = findings_for_section(1, 1, content, self.DIRECTION, self.POINTS, pool)
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert "다시 쓰면" in rows[0]["detail"]
        assert rows[0]["section_ref"] == "1.1"

    def test_missing_without_evidence_warns_about_collection(self):
        content = "국정과제 연계만 서술한다."
        rows = findings_for_section(1, 1, content, self.DIRECTION, self.POINTS, "무관한 근거")
        assert len(rows) == 1
        assert "자료를 먼저 찾으세요" in rows[0]["detail"]
        # 서식 지시일 수도 있으므로 단정하지 않는다(2026-08-11 실측: 8건 중 3건이 서식 지시).
        assert "서술 방식 지시면" in rows[0]["detail"]

    def test_both_branches_split(self):
        content = "국정과제 연계만 서술한다."
        rows = findings_for_section(
            1, 1, content, self.DIRECTION, self.POINTS, "글로벌 경쟁 현황 자료"
        )
        assert len(rows) == 2
        details = " ".join(r["detail"] for r in rows)
        assert "글로벌 경쟁 현황" in details and "사업 추진 시급성" in details

    def test_no_outline_directives_is_silent(self):
        assert findings_for_section(1, 1, "본문", "", [], "") == []
