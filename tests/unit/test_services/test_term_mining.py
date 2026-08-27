"""용어 채굴(indexing/terms) — 패턴·병합·LLM 검증의 결정적 부분만(DB·네트워크 없음)."""

from __future__ import annotations

from src.services.indexing.terms import (
    _validate_llm_terms,
    merge_entries,
    mine_term_patterns,
    term_key,
)

# 실측 병리의 원문(RE100 리포팅 가이던스 p.20) — 이 정의를 못 캐면 이 기능은 의미가 없다.
_RE100_DEF = (
    "Column 11: Supply arrangement start year - equivalent to operational commencement "
    "and must be prior to 2024. This is compared with the commissioning year."
)


class TestPatterns:
    def test_equivalent_to_captures_term_and_sentence(self):
        entries = mine_term_patterns(_RE100_DEF)
        hit = next(e for e in entries if e["en"] == "operational commencement")
        # 접속·조동사 꼬리("and must be")가 용어에 딸려 오지 않는다
        assert hit["definition"].startswith("Column 11: Supply arrangement start year")
        assert "prior to 2024" in hit["definition"]
        # 다음 문장은 정의에 섞이지 않는다
        assert "commissioning" not in hit["definition"]

    def test_ko_en_abbr_triple(self):
        entries = mine_term_patterns(
            "에너지속성인증서(Energy Attribute Certificate, EAC)로 뒷받침되는 재생전력"
        )
        hit = entries[0]
        assert hit["ko"] == "에너지속성인증서"
        assert hit["en"] == "Energy Attribute Certificate"
        assert hit["abbr"] == "EAC"

    def test_ko_fragment_prefix_trimmed(self):
        # 괄호 앞 문맥이 딸려 오면 조사·연결어미 뒤 어절만 용어로 남는다
        entries = mine_term_patterns("설비를 보유하는 동시에 잉카(Ingka) 그룹은")
        assert entries[0]["ko"] == "잉카"

    def test_en_abbr_pair(self):
        entries = mine_term_patterns("under the Carbon Border Adjustment Mechanism (CBAM) rules")
        hit = next(e for e in entries if e["abbr"] == "CBAM")
        assert hit["en"] == "Carbon Border Adjustment Mechanism"

    def test_common_abbr_excluded(self):
        assert mine_term_patterns("인공지능(AI) 기반 분석") == []

    def test_korean_legal_definition(self):
        entries = mine_term_patterns(
            '"온실가스"란 적외선 복사열을 흡수하거나 재방출하는 기체를 말한다.'
        )
        hit = next(e for e in entries if e["ko"] == "온실가스")
        assert "말한다" in hit["definition"]

    def test_korean_definition_rejects_iran_style_false_positive(self):
        # "미국과 이란(Iran) …말한다"는 정의 문형이 아니다 — 정의의 '란'은 명사에
        # 직접 붙고, 붙여 읽은 "미국과 이"는 마지막 어절 1자 검사에서 떨어진다
        entries = mine_term_patterns("미국과 이란 오랜 협상을 이어 왔다고 말한다")
        assert entries == []

    def test_quoted_means_definition(self):
        entries = mine_term_patterns(
            "'market boundary' means the geographic area within which EACs may be claimed."
        )
        hit = next(e for e in entries if e["en"] == "market boundary")
        assert "geographic area" in hit["definition"]


class TestMerge:
    def test_merge_fills_missing_fields_pattern_first(self):
        pattern = [
            {
                "ko": None,
                "en": "operational commencement",
                "abbr": None,
                "definition": "원문 정의",
                "origin": "pattern",
            }
        ]
        llm = [
            {
                "ko": "공급 개시",
                "en": "Operational Commencement",
                "abbr": None,
                "definition": "다른 정의",
                "origin": "llm",
            }
        ]
        merged = merge_entries(pattern, llm)
        assert len(merged) == 1
        assert merged[0]["definition"] == "원문 정의"  # 패턴이 정본
        assert merged[0]["ko"] == "공급 개시"  # LLM은 빈 칸만 채운다

    def test_merge_links_via_abbr(self):
        a = [
            {
                "ko": None,
                "en": "Energy Attribute Certificate",
                "abbr": "EAC",
                "definition": None,
                "origin": "pattern",
            }
        ]
        b = [
            {
                "ko": "에너지속성인증서",
                "en": None,
                "abbr": "EAC",
                "definition": None,
                "origin": "pattern",
            }
        ]
        merged = merge_entries(a, b)
        assert len(merged) == 1
        assert merged[0]["ko"] == "에너지속성인증서"
        assert merged[0]["en"] == "Energy Attribute Certificate"


class TestLlmValidation:
    def test_fabricated_definition_dropped_entry_kept(self):
        raw = [{"en": "grandfathering", "definition": "문서에 없는 지어낸 정의다"}]
        out = _validate_llm_terms(raw, "grandfathering is mentioned but never defined here")
        assert out[0]["en"] == "grandfathering"
        assert out[0]["definition"] is None

    def test_verbatim_definition_survives_whitespace_differences(self):
        src = "Additionality means  the project\nwould not have occurred otherwise."
        raw = [
            {
                "en": "Additionality",
                "definition": "Additionality means the project would not have occurred otherwise.",
            }
        ]
        out = _validate_llm_terms(raw, src)
        assert out[0]["definition"] is not None

    def test_rejects_non_dict_and_empty(self):
        assert _validate_llm_terms("문자열", "본문") == []
        assert _validate_llm_terms([{"definition": "정의만 있고 용어가 없다"}], "본문") == []


def test_term_key_prefers_en():
    assert term_key({"en": "market boundary", "abbr": "MB", "ko": "시장경계"}) == "market boundary"
    assert term_key({"en": None, "abbr": "EAC", "ko": None}) == "EAC"
