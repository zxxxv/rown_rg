"""계산 지표에 값이 없는 절 — 어휘가 아니라 값을 본다(2026-08-27 예타 실측에서 나온 구멍)."""

from __future__ import annotations

from src.services.qa.metric_values import (
    metric_findings,
    metric_value_hits,
    missing_metric_values,
)


class _Plan:
    def __init__(self, chapter: int, number: int) -> None:
        self.chapter_number = chapter
        self.section_number = number


class TestMissingMetricValues:
    def test_threshold_statement_is_not_a_value(self):
        # "B/C ≥ 1.000이면 타당"의 1.000은 판정 문턱이지 이 사업의 계산 결과가 아니다.
        body = "판정 기준은 ①B/C ≥ 1.000, ②NPV ≥ 0, ③IRR ≥ 사회적 할인율이다"
        assert "B/C" in missing_metric_values(body)

    def test_citation_marker_is_not_a_value(self):
        # 실제로 이렇게 빠져나갔다 - (출처 1)의 1을 B/C 값으로 읽었다.
        body = "지연 시나리오는 B/C 하락 폭이 큰 축에 해당함(출처 1)"
        assert missing_metric_values(body) == ["B/C"]

    def test_computed_value_passes(self):
        body = "총편익 1조 2,400억 원 ÷ 총비용 1조 96억 원 = B/C 1.23으로 산출됨"
        assert missing_metric_values(body) == []

    def test_unrelated_section_is_silent(self):
        assert missing_metric_values("탄소규제 대응 현황을 정리하였다") == []

    def test_empty_content(self):
        assert missing_metric_values("") == []

    def test_method_only_section_reports_every_named_metric(self):
        body = (
            "동 사업의 경제성 종합지표는 비용편익비율(B/C), 순현재가치(NPV), "
            "내부수익률(IRR) 세 지표를 병렬 산출하고, 세 지표가 동시에 판정 기준을 "
            "충족하는지를 기준으로 경제적 타당성을 해석함"
        )
        missing = missing_metric_values(body)
        assert {"B/C", "NPV", "IRR", "비용편익비율", "순현재가치", "내부수익률"} <= set(missing)


class TestMetricValueHits:
    def test_counts_only_value_bearing_mentions(self):
        body = "B/C ≥ 1.000 기준이며, 산출 결과 B/C 1.23이다"
        assert metric_value_hits(body, "B/C") == 1


class TestMetricFindings:
    def test_flags_the_section_as_critical(self):
        rows = metric_findings([(_Plan(6, 3), "B/C ≥ 1.000이면 타당으로 판정한다")])
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"
        assert rows[0]["section_ref"] == "6.3"
        assert rows[0]["category"] == "수치 산출 누락"

    def test_silent_when_values_present(self):
        rows = metric_findings([(_Plan(6, 3), "B/C 1.23, NPV 4,200억 원, IRR 8.4%")])
        assert rows == []
