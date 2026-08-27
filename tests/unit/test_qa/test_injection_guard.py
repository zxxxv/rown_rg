"""주입 가드의 결정적 부품 — 연도 근접 판정과 코퍼스 위치 탐색의 매칭 자.

배경(2026-08-27 v6 near_miss 전수 라벨링): "주입 가족"으로 지목됐던 424·428·545·
289가 전부 코퍼스에 실재했다 - 진짜 병리는 자료 시점 병존+오귀속이었고, 가드가
실재를 의심으로 만든 원인은 넷이었다. 각각을 여기 고정한다:

  ① 경계 없는 find - "21"이 "2021" 안에, "0.3"이 "10.3" 안에 걸렸다
  ② 창 80자 - 표는 연도가 머리행에, 산문은 기준연도가 문단 앞에 온다(실측 80~240)
  ③ 짧은 가수 누락 - "91억"이 "USD 9.1 billion"으로 적힌 코퍼스를 못 찾아
     실재 수치가 critical 창작으로 부활했다(같은 날 number_variants 수술의 회귀)
  ④ 축약 연도 - 자료가 "'21년 말"로 적으면 "2021" 문자열 대조가 실패한다
"""

from __future__ import annotations

from src.services.qa.evidence_findings import _YEAR_WINDOW, _year_beside, injection_rows
from src.services.qa.gate import locate_probes, match_patterns, normalize_haystack


class TestYearBeside:
    def test_boundary_no_match_inside_other_numbers(self) -> None:
        # "21"은 "2021" 안의 토막이 아니라 진짜 등장이어야 한다.
        assert not _year_beside("Since 2021 regulated installations must follow", "21", ("2017",))
        # "0.3"이 "10.3" 안에 걸리면 안 된다.
        assert not _year_beside("| 2021 | 5.7 | 10.3 | 73% |", "0.3", ("2017",))

    def test_real_occurrence_near_year(self) -> None:
        assert _year_beside("2021년 말 기준 REC 거래는 판매량의 0.3% 수준이었다", "0.3", ("2021",))

    def test_window_covers_table_head_distance(self) -> None:
        # 표 머리 연도 - 값과 연도 사이 100자쯤은 같은 표다(v6 289TWh 실측 무늬).
        filler = "| 항목 | 값 |" + " 열 " * 30
        text = f"기준연도 2023 집계표 {filler} 재생e 사용량 289 TWh"
        assert len(text) - text.find("289") < _YEAR_WINDOW
        assert _year_beside(text, "289", ("2023",))

    def test_abbreviated_year_forms(self) -> None:
        # 자료가 "'21년"으로 적어도 2021로 본다(v6 K-RE100 74개사 실측).
        assert _year_beside("’21년 말 참여기업 74개사 집계", "74", ("2021",))
        assert _year_beside("'21년 말 참여기업 74개사 집계", "74", ("2021",))

    def test_far_year_is_not_beside(self) -> None:
        text = "2020년 서두. " + ("무관한 서술. " * 60) + " 값은 1.3으로 집계"
        assert not _year_beside(text, "1.3", ("2020",))


class TestLocateMatching:
    def test_short_mantissa_found_by_scale_word(self) -> None:
        """ "91억"은 코퍼스에 "USD 9.1 billion"으로 적힌다 - 낱말 패턴까지 있어야
        재검색이 찾고, 못 찾으면 실재 수치가 critical 창작이 된다."""
        assert "9.1" in locate_probes("91억")
        hay = normalize_haystack("total revenues reached USD 9.1 billion in 2024")
        assert any(p.search(hay) for p in match_patterns("91억"))

    def test_boundary_still_applies(self) -> None:
        # 경계는 유지 - "9.1"이 "39.1" 안에 걸리면 안 된다.
        hay = normalize_haystack("index rose 39.1 points in 2024")
        assert not any(p.search(hay) for p in match_patterns("91억"))

    def test_plain_korean_form_matches(self) -> None:
        hay = normalize_haystack("사업비는 총 91억 원 규모다")
        assert any(p.search(hay) for p in match_patterns("91억"))


class TestInjectionRows:
    """문장 옆 주입 의심 행 - 근거 화면이 "지어냈거나 옛 지식"을 그 자리에서 말하게."""

    def test_located_elsewhere_reports_title(self) -> None:
        rows = injection_rows(
            ["428"],
            ("2023",),
            located={"428": "제조 수출기업 실태"},
            injected={"428"},
            relocated_norms=set(),
        )
        assert rows == [("428", "제조 수출기업 실태")]

    def test_nowhere_reports_none(self) -> None:
        rows = injection_rows(["777"], ("2023",), located={}, injected=set(), relocated_norms=set())
        assert rows == [("777", None)]

    def test_relocated_token_skipped(self) -> None:
        # 절 풀에서 이미 소재를 찾은 수치는 "출처 n에 있습니다"가 말한다 - 이중 경고 금지.
        rows = injection_rows(
            ["428"], ("2023",), located={"428": "x"}, injected={"428"}, relocated_norms={"428"}
        )
        assert rows == []

    def test_no_year_no_rows(self) -> None:
        # 연도 명시가 주입 서명의 축 - 연도 없는 무근거는 다른 경고(빨간 줄) 몫.
        assert injection_rows(["777"], (), located={}, injected=set(), relocated_norms=set()) == []

    def test_grounded_in_corpus_near_year_not_flagged(self) -> None:
        # 소재도 있고 injected도 아니면(연도 곁 확인됨) 의심이 아니다.
        rows = injection_rows(
            ["289"],
            ("2023",),
            located={"289": "신재생 해외이슈"},
            injected=set(),
            relocated_norms=set(),
        )
        assert rows == []
