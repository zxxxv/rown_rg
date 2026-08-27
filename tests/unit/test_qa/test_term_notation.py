"""용어 병기 대조(qa/term_notation) — 불일치·요동·정의 검토, 그리고 무소음 조건."""

from __future__ import annotations

from src.services.qa.term_notation import extract_pairs, term_notation_findings


def _entry(**over):
    return {
        "ko": None,
        "en": None,
        "abbr": None,
        "definition": None,
        "source_title": "RE100 가이던스",
        "source_id": "s1",
        **over,
    }


class TestExtractPairs:
    def test_triple_and_bare_abbr(self):
        pairs = extract_pairs(
            "에너지속성인증서(Energy Attribute Certificate, EAC)와 재생에너지 인증서(EAC)"
        )
        assert ("에너지속성인증서", "Energy Attribute Certificate", "EAC") in pairs
        assert ("재생에너지 인증서", "EAC", "EAC") in pairs  # 여러 어절 용어는 그대로 남는다

    def test_citation_and_unit_parens_ignored(self):
        assert extract_pairs("비중이 급증함(출처 37)\n(단위: %)\n비율 (B/C) 분석") == []

    def test_lowercase_term_pair(self):
        pairs = extract_pairs("2024년 이전에 사업 개시(operational commencement)된 계약")
        assert pairs == [("사업 개시", "operational commencement", None)]


class TestFindings:
    def test_mismatch_against_table(self):
        sections = [(1, "1.1", "재생에너지 인증서(EAC)로 뒷받침되는 조달")]
        entries = [_entry(ko="에너지속성인증서", en="Energy Attribute Certificate", abbr="EAC")]
        found = term_notation_findings(sections, entries)
        assert len(found) == 1
        f = found[0]
        assert f["category"] == "용어 표기 불일치"
        assert "재생에너지 인증서" in f["detail"]
        assert "에너지속성인증서" in f["detail"]
        assert f["section_ref"] == "1.1"

    def test_matching_notation_is_silent(self):
        sections = [(1, "1.1", "에너지속성인증서(EAC) 기반 조달")]
        entries = [_entry(ko="에너지속성인증서", abbr="EAC")]
        assert term_notation_findings(sections, entries) == []

    def test_variance_within_report_without_table(self):
        # 녹색전력 vs 녹색에너지 - 접미 관계가 아닌 진짜 표기 요동만 남는다.
        sections = [
            (1, "1.1", "녹색전력인증서(GEC)를 활용"),
            (3, "3.2", "녹색에너지인증서(GEC)를 구매"),
        ]
        found = term_notation_findings(sections, [])
        assert len(found) == 1
        f = found[0]
        assert f["category"] == "용어 표기 요동"
        assert "2가지" in f["detail"]
        assert "1.1" in f["detail"] and "3.2" in f["detail"]

    def test_variance_collapses_spacing_and_context_suffix(self):
        # 띄어쓰기 차이·문맥 접미는 요동이 아니다(v6 실측 오탐 유형) - 경고 없음.
        sections = [
            (1, "1.1", "에너지속성인증서(EAC)를 활용"),
            (2, "2.1", "에너지 속성인증서(EAC)를 구매"),
            (3, "3.2", "분리되어 기술자문그룹(TAG)이 맡고, 기술자문그룹(TAG)은 심의한다"),
        ]
        assert term_notation_findings(sections, []) == []

    def test_short_representative_does_not_absorb(self):
        # "인증서"(3자)가 ○○인증서들을 삼키면 진짜 구분까지 뭉개진다 - 흡수 하한 4자.
        sections = [
            (1, "1.1", "재생에너지공급인증서(REC)와 인증서(REC)"),
        ]
        found = term_notation_findings(sections, [])
        assert len(found) == 1
        assert found[0]["category"] == "용어 표기 요동"

    def test_mismatch_tolerates_spacing_and_context_suffix(self):
        # 용어표 대조도 같은 눈금 - 띄어쓰기·문맥 접미는 불일치가 아니다.
        sections = [(1, "1.1", "자료 기준 재생에너지 공급인증서(REC)에 따르면")]
        entries = [_entry(ko="재생에너지공급인증서", abbr="REC")]
        assert term_notation_findings(sections, entries) == []

    def test_defined_term_review_row(self):
        # v6 실측 병리 - 정의만 있고 한글 표기가 없는 용어는 기계 판정이 불가능해
        # 사람이 훑을 검토 목록 한 행으로만 세운다.
        sections = [(2, "2.4", "2024년 이전에 사업 개시(operational commencement)된 계약")]
        entries = [
            _entry(
                en="operational commencement",
                definition="Supply arrangement start year - equivalent to operational commencement",
            )
        ]
        found = term_notation_findings(sections, entries)
        assert len(found) == 1
        f = found[0]
        assert f["category"] == "정의 용어 표기 검토"
        assert '"사업 개시(operational commencement)" [2.4]' in f["detail"]

    def test_flagged_term_not_repeated_in_review(self):
        # 불일치로 이미 경고한 용어는 검토 행에 다시 싣지 않는다.
        sections = [(1, "1.1", "재생 인증서(EAC) 조달")]
        entries = [_entry(ko="에너지속성인증서", abbr="EAC", definition="EAC means ...")]
        found = term_notation_findings(sections, entries)
        assert [f["category"] for f in found] == ["용어 표기 불일치"]

    def test_no_pairs_no_findings(self):
        assert term_notation_findings([(1, "1.1", "병기가 전혀 없는 본문")], []) == []
