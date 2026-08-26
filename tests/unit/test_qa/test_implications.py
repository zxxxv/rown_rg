"""시사점 절 두 규칙 — 2026-08-27 실측으로 정밀도를 맞춘 판정."""

from __future__ import annotations

from src.services.qa.implications import (
    actorless_recommendations,
    implication_findings,
    new_numbers,
)


class _Plan:
    def __init__(self, chapter: int, number: int, title: str) -> None:
        self.chapter_number = chapter
        self.section_number = number
        self.title = title


class TestNewNumbers:
    def test_value_present_earlier_is_not_new(self):
        assert new_numbers("감축률은 24.3%였다", "앞 절에서 24.3% 확인") == []

    def test_notation_variant_is_the_same_value(self):
        # 492.5억 == 492억 5,000만 원. 문자열로 재면 창작으로 잡힌다(골든 v1 오탐).
        assert new_numbers("예산 492.5억", "사업비 492억 5,000만 원") == []

    def test_genuinely_new_value_is_reported(self):
        assert "31.6%" in new_numbers("도입 용이성 31.6%", "앞 절에는 다른 값 12.4%뿐")

    def test_section_number_in_heading_is_not_a_value(self):
        # "## 3.7 역량분석 시사점"의 3.7을 새 수치로 잡던 오탐.
        assert new_numbers("## 3.7 역량분석 시사점\n본문", "앞 절") == []

    def test_citation_number_is_not_a_value(self):
        # "(출처 35, 51 중 …)"의 51을 새 수치로 잡던 오탐.
        assert new_numbers("낮음(출처 35, 51 중 사용 가능)", "앞 절") == []

    def test_empty_content(self):
        assert new_numbers("", "앞 절 본문") == []


class TestActorlessRecommendations:
    def test_flags_obligation_without_actor(self):
        body = "- 대상 품목 커버리지가 업종별로 상이한 점도 노출도 판정에 반영해야 함"
        assert len(actorless_recommendations(body)) == 1

    def test_actor_present_is_silent(self):
        body = "- 주관부처는 대상 품목 커버리지를 노출도 판정에 반영해야 함"
        assert actorless_recommendations(body) == []

    def test_country_counts_as_actor(self):
        # "EU는 …해야 함"을 주체 없음으로 잡던 오탐.
        assert actorless_recommendations("EU는 전 부문으로 범위를 확대해야 함") == []

    def test_negation_is_not_an_obligation(self):
        # "~할 필요가 없으며"는 하지 말라는 뜻이다.
        body = "- 훈련과 추론은 동일한 하드웨어로 실행할 필요가 없으며 분리가 가능함"
        assert actorless_recommendations(body) == []

    def test_plain_fact_is_not_an_obligation(self):
        body = "ㅇ 업종별로 RE100 인지 수준과 관심 동인이 뚜렷하게 갈리는 것으로 나타남"
        assert actorless_recommendations(body) == []


class TestImplicationFindings:
    def test_only_implication_titled_sections_are_checked(self):
        rows = implication_findings(
            [
                (_Plan(1, 1, "개요"), "여기서 처음 나오는 값 88.8%"),
                (_Plan(1, 2, "상세 분석"), "본문"),
            ]
        )
        assert rows == []

    def test_prior_sections_form_the_allowed_pool(self):
        rows = implication_findings(
            [
                (_Plan(1, 1, "개요"), "감축률 24.3% 확인"),
                (_Plan(1, 5, "시사점"), "감축률은 24.3%로 확인되었다"),
            ]
        )
        assert rows == []

    def test_reports_new_number_as_warning(self):
        rows = implication_findings(
            [
                (_Plan(1, 1, "개요"), "다른 값 12.4%"),
                (_Plan(1, 5, "시사점"), "새 통계 31.6%가 확인됨"),
            ]
        )
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert rows[0]["category"] == "시사점 새 수치"
        assert rows[0]["section_ref"] == "1.5"


class TestOrderIndependence:
    """호출부가 순서를 섞어 줘도 판정이 같아야 한다 - 규칙 ①은 앞 절이 전부다."""

    def test_sorts_by_outline_position(self):
        shuffled = [
            (_Plan(1, 5, "시사점"), "감축률은 24.3%로 확인되었다"),
            (_Plan(1, 1, "개요"), "감축률 24.3% 확인"),
        ]
        assert implication_findings(shuffled) == []
