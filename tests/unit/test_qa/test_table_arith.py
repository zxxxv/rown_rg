"""표 합산·복제·빈 목록 검산 3종 — 2026-09-04 철강 R&D 정독 실측 결함의 회귀 가드.

세 검출기 모두 정밀도 우선: 실측에서 재현된 양성과, 정상 표가 안 걸리는 음성을
함께 고정한다.
"""

from __future__ import annotations

from src.services.qa.table_check import (
    declared_lists_unfilled,
    table_duplicate_row_cells,
    table_total_mismatches,
)

_MATCH_TABLE = """| 중과제 | 매칭 건수 |
|---|---|
| ①-1 | 16건 |
| ①-2 | 8건 |
| ①-3 | 3건 |
| ①-4 | 13건 |
| ①-5 | 5건 |
"""


class TestTotalMismatch:
    def test_본문_총계_주장이_열_합과_다르면_잡는다(self) -> None:
        md = _MATCH_TABLE + "\n- 내역① 전체 수요 매칭은 87건으로 가장 큰 비중임"
        out = table_total_mismatches(md)
        assert any("87" in x and "45" in x for x in out), out

    def test_총계가_맞으면_조용하다(self) -> None:
        md = _MATCH_TABLE + "\n- 내역① 전체 수요 매칭은 총 45건임"
        assert table_total_mismatches(md) == []

    def test_합계_행_검산(self) -> None:
        md = "| 항목 | 값 |\n|---|---|\n| 가 | 10 |\n| 나 | 20 |\n| 다 | 30 |\n| 합계 | 70 |"
        out = table_total_mismatches(md)
        assert any("60" in x and "70" in x for x in out), out

    def test_합계_행이_정합이면_조용하다(self) -> None:
        md = "| 항목 | 값 |\n|---|---|\n| 가 | 10 |\n| 나 | 20 |\n| 다 | 30 |\n| 합계 | 60 |"
        assert table_total_mismatches(md) == []

    def test_덧셈식_오류를_잡는다(self) -> None:
        out = table_total_mismatches("합계는 16+8+3 = 30건임")
        assert any("덧셈식" in x for x in out), out

    def test_옳은_덧셈식은_조용하다(self) -> None:
        assert table_total_mismatches("16+8+3+13+5 = 45건으로 검산됨") == []

    def test_밴드_밖_총계는_다른_지표로_보고_넘긴다(self) -> None:
        # 표는 건수(합 45), 주장은 사업비 총 5,000 - ×10 밖이라 짝짓지 않는다.
        md = _MATCH_TABLE + "\n- 총사업비는 전체 5,000억 원 규모의 과제임"
        assert table_total_mismatches(md) == []


class TestDuplicateRowCells:
    def test_전치형_표의_옆_칸_복제(self) -> None:
        md = (
            "| 구분 | EU CBAM | 미국 GSSA |\n|---|---|---|\n"
            "| 목적 | 자국 제조업 보호 및 2050 탄소중립 실현 "
            "| 자국 제조업 보호 및 2050 탄소중립 실현 |\n"
            "| 시점 | 2026 본시행 | 미정 |"
        )
        out = table_duplicate_row_cells(md)
        assert any("목적" in x for x in out), out

    def test_행_간_복제(self) -> None:
        md = (
            "| 지표 | 확정 주체 |\n|---|---|\n"
            "| 지표 1 | 총괄기획위원회 심의로 확정함 |\n"
            "| 지표 2 | 총괄기획위원회 심의로 확정함 |"
        )
        assert len(table_duplicate_row_cells(md)) == 1

    def test_짧은_반복_값은_정상이다(self) -> None:
        md = "| 항목 | 여부 |\n|---|---|\n| 가 | 해당 없음 |\n| 나 | 해당 없음 |"
        assert table_duplicate_row_cells(md) == []


class TestDeclaredLists:
    def test_형제_불릿은_항목이_아니다(self) -> None:
        # 실측 놓침 재현: 선언 두 줄 뒤 같은 깊이 ㅇ 불릿을 항목으로 오인했었다.
        md = "ㅇ 확정 개발기술 목록(20개 품목)\n\n\nㅇ 이 중 일부는 이미 매핑된 바 있음"
        assert len(declared_lists_unfilled(md)) == 1

    def test_깊은_마커_항목은_채워진_것이다(self) -> None:
        md = "ㅇ 목록(3개 품목)\n- 품목 A\n- 품목 B\n- 품목 C"
        assert declared_lists_unfilled(md) == []

    def test_표로_채운_목록도_인정한다(self) -> None:
        md = "ㅇ 목록(2개 품목)\n| 품목 | 단가 |\n|---|---|\n| A | 1 |\n| B | 2 |"
        assert declared_lists_unfilled(md) == []
