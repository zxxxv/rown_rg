"""발간연도 추출 — 자료 시점 축의 재료 (검증런 실코퍼스 파일명이 회귀셋).

발간연도 != 데이터 기준연도 - 여기서 뽑는 것은 발간까지고, 확신 없으면 None이다.
"""

from __future__ import annotations

from src.services.indexing.published_year import extract_published_year, year_from_page_age


class TestTitleExtraction:
    def test_filename_with_dot_date(self):
        assert extract_published_year("KITA 통상리포트 10호_2023.10.pdf", "") == 2023

    def test_filename_latest_year_wins(self):
        # FY 2024-25 회계연도보다 서명일(March 2026)이 발간 시점이다 - 최신 채택.
        name = "RE100 Annual Report_FY 2024-25_FINAL_17 March 2026_SIGNED (1) (1).pdf"
        assert extract_published_year(name, "") == 2026

    def test_timestamp_fragments_not_years(self):
        # 파일명 타임스탬프의 부분열("2918" 등)은 경계 가드·상한에 걸러진다.
        assert extract_published_year("A38B4BB2CA_1712575062122_2918.pdf", "") is None

    def test_target_year_beyond_cap_ignored(self):
        # "2030 전망" 같은 목표연도는 발간연도가 아니다(상한=내년).
        assert extract_published_year("탄소중립 2030 전망 보고서(2024).pdf", "") == 2024


class TestHeadExtraction:
    def test_labeled_publication_date_first(self):
        head = "본 보고서는 산업 동향을 다룬다.\n발행일: 2022년 3월 15일\n2030년 목표를 분석한다."
        assert extract_published_year(None, head) == 2022

    def test_korean_date_in_head(self):
        assert extract_published_year(None, "2024. 8. 발간사\n서론…") == 2024

    def test_english_month_date(self):
        assert extract_published_year(None, "Washington, DC. March 2026. Working Paper…") == 2026

    def test_bare_years_in_body_not_used(self):
        # 본문의 맨 연도(통계·목표연도)는 발간연도로 오인하지 않는다 - '월'까지 있어야 날짜다.
        assert extract_published_year(None, "2018년 통계에 따르면 시장은 성장했다") is None
        assert extract_published_year(None, "2018 기준 시장은 성장했다") is None


class TestPageAge:
    def test_iso_and_prose_forms(self):
        assert year_from_page_age("2024-08-01") == 2024
        assert year_from_page_age("August 1, 2024") == 2024
        assert year_from_page_age(None) is None
        assert year_from_page_age("3 days ago") is None
