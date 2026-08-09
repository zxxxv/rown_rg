"""pgroonga 질의 재작성 — 공백 AND가 키워드 검색을 죽이던 문제.

실측(2026-08-10, 예타 런): 절 제목을 그대로 &@~에 넣으면 12절 중 10절이 0건이었다.
제목의 모든 단어가 한 청크에 다 있어야 매칭되기 때문이다. OR로 묶으면 같은 절이
49~753건을 회수한다 — 하이브리드의 키워드 절반이 그동안 놀고 있었다.
"""

from __future__ import annotations

from src.services.retrieval._keyword import to_or_query


class TestToOrQuery:
    def test_multiword_title_becomes_or(self):
        assert to_or_query("특허 출원 동향") == "특허 OR 출원 OR 동향"

    def test_stopwords_dropped(self):
        # '및'은 어느 문서에나 있어 변별력이 없다
        assert to_or_query("기술 동향 및 인프라 현황") == "기술 OR 동향 OR 인프라 OR 현황"

    def test_single_token_unchanged_in_meaning(self):
        assert to_or_query("반도체") == "반도체"

    def test_punctuation_and_syntax_chars_stripped(self):
        # 질의 문법 주입(따옴표·괄호)도 토큰 정규식에서 함께 떨어진다
        assert to_or_query("거시환경 분석 (STEEP)") == "거시환경 OR 분석 OR STEEP"
        assert '"' not in to_or_query('시장 "규모"')

    def test_no_usable_token_falls_back_to_original(self):
        assert to_or_query("A") == "A"
