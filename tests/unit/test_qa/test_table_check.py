"""표 셀 수치 검사 — 문장 검사망에서 빠져 있던 표의 결정적 대조.

정밀도 우선이 계약이다: 애매한 쌍(라벨 불일치·형 불일치·반올림 차이)은 경고하지
않는다. 오탐 폭탄은 검사를 죽인다(critical 26건 중 22건 오탐 실사고).
"""

from __future__ import annotations

from src.services.qa.table_check import (
    table_numeric_cells,
    table_prose_mismatches,
    table_ungrounded_numbers,
)

_TABLE = """주요 플랫폼 비교

| 플랫폼 | 국내 이용 경험률 | 월간 이용자 |
|---|---|---|
| 유튜브 쇼츠 (YouTube Shorts) | 87.6% | 20억 명 |
| 인스타그램 릴스 | 59.4% | - |
"""


class TestTableNumericCells:
    def test_extracts_cells_with_labels(self):
        cells = table_numeric_cells(_TABLE)
        by_token = {c.token: c for c in cells}
        assert by_token["87.6%"].row_label == "유튜브 쇼츠"  # 괄호 병기 제거
        assert by_token["87.6%"].col_header == "국내 이용 경험률"
        assert by_token["87.6%"].is_percent
        # 단위(억·명)는 토큰에 안 붙는다 - 문장 쪽 significant_numbers와 같은 규약.
        assert by_token["20"].col_header == "월간 이용자"

    def test_year_cells_not_significant(self):
        # 연도 열은 수치 주장이 아니다 - significant_numbers의 제외 규칙을 그대로 쓴다.
        md = "| 구분 | 연도 |\n|---|---|\n| 착수 | 2024 |\n"
        assert table_numeric_cells(md) == []

    def test_no_table_no_cells(self):
        assert table_numeric_cells("표 없는 본문 문단이다.") == []


class TestTableUngrounded:
    def test_grounded_cell_not_flagged(self):
        cited = "설문 결과 응답자의 87.6%가 유튜브 쇼츠를 이용했다. 월간 20억 명 규모."
        out = table_ungrounded_numbers(_TABLE, cited)
        assert all("87.6" not in t for t in out)
        assert all("20억" not in t for t in out)

    def test_missing_cell_flagged_with_location(self):
        cited = "유튜브 쇼츠 이용률은 87.6%다. 월간 20억 명."
        out = table_ungrounded_numbers(_TABLE, cited)
        # 59.4%는 근거에 없다 - 어느 셀인지(라벨/열)를 함께 알린다.
        assert any(t.startswith("59.4%") and "인스타그램 릴스" in t for t in out)

    def test_comma_normalization(self):
        md = "| 항목 | 값 |\n|---|---|\n| 수출액 | 3,200억 |\n"
        assert table_ungrounded_numbers(md, "수출액은 3200억 원이다.") == []

    def test_empty_cited_content_silent(self):
        # 인용 근거가 없으면(옛 절 등) 비교 자체가 성립하지 않는다 - 경고하지 않는다.
        assert table_ungrounded_numbers(_TABLE, "  ") == []


class TestTableProseMismatch:
    def test_conflicting_value_flagged(self):
        md = (
            "| 구분 | 전력사용량 |\n|---|---|\n| 삼성전자 | 289TWh |\n\n"
            "ㅇ 삼성전자의 전력사용량은 240TWh로 집계된 것으로 나타났음 [1]\n"
        )
        out = table_prose_mismatches(md)
        assert len(out) == 1
        assert "삼성전자" in out[0] and "289" in out[0] and "240" in out[0]

    def test_consistent_value_not_flagged(self):
        md = (
            "| 구분 | 전력사용량 |\n|---|---|\n| 삼성전자 | 240TWh |\n\n"
            "ㅇ 삼성전자의 전력사용량은 240TWh로 집계된 것으로 나타났음 [1]\n"
        )
        assert table_prose_mismatches(md) == []

    def test_rounding_difference_not_flagged(self):
        # 표 45.8% vs 본문 '약 46%'는 같은 값이다 - 반올림 차이는 불일치가 아니다.
        md = (
            "| 구분 | 점유율 |\n|---|---|\n| 인스타그램 릴스 | 45.8% |\n\n"
            "ㅇ 인스타그램 릴스의 점유율은 전체의 약 46%로 나타났음 [1]\n"
        )
        assert table_prose_mismatches(md) == []

    def test_korean_number_split_not_flagged(self):
        # 표 482.7 vs 본문 '482억 7,000만'은 같은 값이다 - 억/만 분해가 오탐 1순위였다.
        md = (
            "| 조사 기관 | 시장 규모 |\n|---|---|\n| Straits Research | 482.7억 달러 |\n\n"
            "ㅇ Straits Research 조사에서 시장 규모는 482억 7,000만 달러로 나타났음 [1]\n"
        )
        assert table_prose_mismatches(md) == []

    def test_shape_mismatch_not_compared(self):
        # 셀은 퍼센트인데 문장의 수치는 금액 - 다른 지표라 비교하지 않는다.
        md = (
            "| 구분 | 점유율 |\n|---|---|\n| 인스타그램 릴스 | 45.8% |\n\n"
            "ㅇ 인스타그램 릴스의 점유율 확대로 광고 매출은 3,200억 원에 이른 것으로 나타났음 [1]\n"
        )
        assert table_prose_mismatches(md) == []

    def test_label_absent_not_compared(self):
        # 라벨이 문장에 없으면 같은 지표라는 확신이 없다 - 판정하지 않는다.
        md = (
            "| 구분 | 전력사용량 |\n|---|---|\n| 삼성전자 | 289TWh |\n\n"
            "ㅇ 국내 반도체 업계의 전력사용량은 240TWh로 집계된 것으로 나타났음 [1]\n"
        )
        assert table_prose_mismatches(md) == []

    def test_col_header_absent_not_compared(self):
        # 라벨만 겹치고 열 머리(지표명)가 없으면 다른 지표다 - '미국' 같은 일반
        # 라벨이 온갖 문장과 짝지어지던 오탐(로컬 실측 대부분)의 재발 방지.
        md = (
            "| 구분 | 전력사용량 |\n|---|---|\n| 삼성전자 | 289TWh |\n\n"
            "ㅇ 삼성전자의 반도체 매출은 240조 원으로 집계된 것으로 나타났음 [1]\n"
        )
        assert table_prose_mismatches(md) == []

    def test_marker_numbers_not_counted(self):
        # 문장의 (출처 n) 번호가 수치로 잡혀 불일치가 되면 안 된다.
        md = (
            "| 구분 | 매장 수 |\n|---|---|\n| 편의점 | 30만 |\n\n"
            "ㅇ 편의점 매장 수 통계는 협회가 집계해 발표한 것으로 나타났음 (출처 12)\n"
        )
        assert table_prose_mismatches(md) == []
