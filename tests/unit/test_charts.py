"""차트 스펙 파싱 - 그릴 수 있는 것만 통과시킨다(틀린 그래프는 자리표시자보다 나쁘다)."""

from __future__ import annotations

import pytest

from src.core.charts import ChartSpecError, has_chart_fence, parse_chart_spec, to_fence

_BAR = """
type: bar
title: 주요국 SMR 투자
unit: 억 달러
x: 미국 | 중국 | 한국
series: 투자액 = 120 | 95 | 30
source: 3, 7
"""


class TestParse:
    def test_bar_spec(self):
        spec = parse_chart_spec(_BAR)
        assert spec.type == "bar"
        assert spec.title == "주요국 SMR 투자"
        assert spec.unit == "억 달러"
        assert spec.x == ("미국", "중국", "한국")
        assert spec.series[0].name == "투자액"
        assert spec.series[0].values == (120.0, 95.0, 30.0)
        assert spec.source == (3, 7)

    def test_multiple_series(self):
        spec = parse_chart_spec(
            "type: line\nx: 2023년 | 2024년\nseries: 국내 = 10 | 20\nseries: 해외 = 30 | 40\n"
        )
        assert [s.name for s in spec.series] == ["국내", "해외"]
        assert spec.series[1].values == (30.0, 40.0)

    def test_thousands_separator_survives(self):
        # 한글 보고서 수치는 "3,200억 원"처럼 콤마를 달고 산다 - 값 하나로 읽혀야 한다.
        spec = parse_chart_spec("type: bar\nx: 가 | 나\nseries: 규모 = 3,200억 | 1.5%\n")
        assert spec.series[0].values == (3200.0, 1.5)

    def test_original_table_preserved(self):
        spec = parse_chart_spec(
            "type: bar\nx: 가 | 나\nseries: 값 = 1 | 2\ntable: |\n  | 구분 | 값 |\n  | 가 | 1 |\n"
        )
        assert spec.table == "| 구분 | 값 |\n| 가 | 1 |"


class TestRejects:
    def test_unknown_type(self):
        with pytest.raises(ChartSpecError, match="차트 종류"):
            parse_chart_spec("type: radar\nx: 가 | 나\nseries: 값 = 1 | 2\n")

    def test_single_x_point(self):
        with pytest.raises(ChartSpecError, match="2개 미만"):
            parse_chart_spec("type: bar\nx: 가\nseries: 값 = 1\n")

    def test_series_length_mismatch(self):
        # x축과 값 개수가 어긋나면 그리면 안 된다 - 조용히 잘리면 틀린 그래프가 나간다.
        with pytest.raises(ChartSpecError, match="맞지 않음"):
            parse_chart_spec("type: bar\nx: 가 | 나 | 다\nseries: 값 = 1 | 2\n")

    def test_non_numeric_value(self):
        with pytest.raises(ChartSpecError, match="숫자로 읽을 수 없는"):
            parse_chart_spec("type: bar\nx: 가 | 나\nseries: 값 = 미정 | 2\n")

    def test_no_series(self):
        with pytest.raises(ChartSpecError, match="계열이 없어"):
            parse_chart_spec("type: bar\nx: 가 | 나\n")

    def test_pie_rejects_multiple_series(self):
        with pytest.raises(ChartSpecError, match="원형 차트"):
            parse_chart_spec("type: pie\nx: 가 | 나\nseries: A = 1 | 2\nseries: B = 3 | 4\n")

    def test_pie_rejects_more_slices_than_colors(self):
        # 원형은 조각 하나가 색 하나다 - 상한을 넘기면 색이 돌아 다른 조각이 같은 색이 된다.
        with pytest.raises(ChartSpecError, match="상한"):
            parse_chart_spec(
                "type: pie\nx: 가 | 나 | 다 | 라 | 마 | 바\nseries: 값 = 1 | 2 | 3 | 4 | 5 | 6\n"
            )


class TestHasChartFence:
    def test_detects_fence(self):
        assert has_chart_fence("문단\n\n```chart\ntype: bar\n```\n") is True

    def test_plain_text_is_not_a_chart(self):
        assert has_chart_fence("표: 제목\n\n| 구분 | 값 |\n| 가 | 1 |") is False

    def test_other_language_fence_is_not_a_chart(self):
        assert has_chart_fence("```python\nprint(1)\n```") is False


class TestRoundTrip:
    def test_fence_reparses_to_same_spec(self):
        spec = parse_chart_spec(_BAR)
        fence = to_fence(spec)
        assert fence.startswith("```chart\n") and fence.endswith("\n```")
        body = fence.removeprefix("```chart\n").removesuffix("\n```")
        assert parse_chart_spec(body) == spec

    def test_round_trip_keeps_original_table(self):
        spec = parse_chart_spec(
            "type: bar\nx: 가 | 나\nseries: 값 = 1 | 2\ntable: |\n  | 구분 | 값 |\n  | 가 | 1 |\n"
        )
        body = to_fence(spec).removeprefix("```chart\n").removesuffix("\n```")
        assert parse_chart_spec(body).table == spec.table
