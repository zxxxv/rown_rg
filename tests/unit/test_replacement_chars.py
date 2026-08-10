"""PDF 추출의 대체 문자() 정리 — 화면·임베딩·인용 원문 공통.

실측(2026-08-10): 수집한 정부 PDF 본문의 최대 2.4%가 U+FFFD였다. 글리프를 유니코드로
못 되돌린 자리라 사람도 모델도 못 읽는다.
"""

from __future__ import annotations

from src.clients.parser.base import strip_replacement_chars
from src.workflows.stages import clean_web_markdown

BAD = chr(0xFFFD)


class TestStripReplacementChars:
    def test_clean_text_untouched(self):
        assert strip_replacement_chars("정상 문장입니다.") == "정상 문장입니다."

    def test_sparse_damage_is_removed_in_place(self):
        line = f"2026년 예산은 {BAD}조 원 규모로 확대될 전망이다"
        assert BAD not in strip_replacement_chars(line)
        assert "2026년 예산은" in strip_replacement_chars(line)

    def test_heavily_garbled_line_is_dropped(self):
        # 남은 글자도 못 믿는다 - 부분 제거는 단어를 뭉갠 채 남긴다.
        assert strip_replacement_chars(f"{BAD}{BAD}{BAD} {BAD}{BAD}") == ""

    def test_keeps_other_lines(self):
        text = f"정상 줄\n{BAD}{BAD}{BAD}{BAD}\n또 정상"
        assert strip_replacement_chars(text).split("\n") == ["정상 줄", "또 정상"]


class TestWebPathAppliesIt:
    def test_collected_markdown_is_cleaned(self):
        # 수집 경로(웹·PDF 직접 회수)도 같은 정리를 탄다.
        assert BAD not in clean_web_markdown(f"본문 문장이 여기 충분히 길게 이어집니다 {BAD} 계속")
