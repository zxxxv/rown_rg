"""인용·출처 표기 규약 - (출처 n)=참고 재작성, [n]=원문 그대로 옮긴 직접 인용."""

from __future__ import annotations

from src.core.citations import (
    numbers_in_order,
    renumber,
    source_numbers,
    strip_source_marks,
    to_source_mark,
)


class TestNumbersInOrder:
    def test_both_forms_counted_in_first_appearance_order(self):
        content = '시장은 확대됨 (출처 3). 보고서는 "재편이 불가피"라고 밝힘 [1]. 또 (출처 3)'
        assert numbers_in_order(content) == [3, 1]

    def test_multi_number_source_mark_expands(self):
        assert numbers_in_order("근거가 여럿임 (출처 2, 5, 2)") == [2, 5]

    def test_markdown_link_label_is_not_a_mark(self):
        assert numbers_in_order("[1](https://ex.com) 참고") == []

    def test_no_marks(self):
        assert numbers_in_order("표기가 없는 문장임") == []


class TestRenumber:
    def test_form_preserved_while_number_changes(self):
        content = "확대됨 (출처 1). 원문은 그대로임 [2]."
        assert renumber(content, {1: 7, 2: 9}) == "확대됨 (출처 7). 원문은 그대로임 [9]."

    def test_unmapped_mark_removed_with_leading_space(self):
        assert (
            renumber("근거 있음 (출처 1). 근거 없음 (출처 4).", {1: 7})
            == "근거 있음 (출처 7). 근거 없음."
        )

    def test_partially_mapped_source_keeps_only_mapped(self):
        assert renumber("복합 근거임 (출처 1, 4)", {1: 7}) == "복합 근거임 (출처 7)"

    def test_newline_not_swallowed_when_mark_dropped(self):
        # 앞 공백만 먹고 개행은 남겨야 개조식 항목이 한 줄로 합쳐지지 않는다.
        assert renumber("첫 항목임 (출처 9)\n둘째 항목임", {}) == "첫 항목임\n둘째 항목임"

    def test_same_global_deduped_within_mark(self):
        # 같은 자료의 다른 청크(로컬 1·4)가 한 전역 번호로 합쳐지면 "(출처 7, 7)"이
        # 된다 - 실측 147마커(2026-08-14 탄소규제 런). 중복은 걷어낸다.
        assert renumber("복합 근거임 (출처 1, 4)", {1: 7, 4: 7}) == "복합 근거임 (출처 7)"
        assert renumber("셋 인용임 (출처 1, 4, 2)", {1: 7, 4: 7, 2: 9}) == "셋 인용임 (출처 7, 9)"


class TestStripSourceMarks:
    def test_source_marks_removed_quotes_kept(self):
        content = '확대됨 (출처 7). 보고서는 "재편"이라 밝힘 [9].'
        assert strip_source_marks(content) == '확대됨. 보고서는 "재편"이라 밝힘 [9].'

    def test_multi_number_mark_removed_whole(self):
        assert strip_source_marks("복합 근거임 (출처 7, 9)") == "복합 근거임"


class TestSourceNumbers:
    def test_collects_source_marks_only(self):
        content = "확대됨 (출처 7). 직접 인용임 [3]. 또 (출처 9, 7)"
        assert source_numbers(content) == [7, 9]

    def test_empty_when_only_quotes(self):
        assert source_numbers("직접 인용만 있음 [3]") == []


class TestToSourceMark:
    def test_legacy_brackets_become_source_marks(self):
        # 표기를 나누기 전 본문의 [n]은 예외 없이 '참고했다'는 뜻이었다.
        assert to_source_mark("확대됨 [7].") == "확대됨 (출처 7)."

    def test_markdown_link_untouched(self):
        assert to_source_mark("[1](https://ex.com) 참고") == "[1](https://ex.com) 참고"


class TestStripNonnumericSourceMarks:
    """수치 인용 문장은 (출처 n)을 남기고, 재구성 서술만 걷어낸다(2026-08-21 지시)."""

    def test_numeric_sentence_keeps_mark(self):
        from src.core.citations import strip_nonnumeric_source_marks

        text = "참여기업 비율은 16.9%로 나타남(출처 10)"
        assert strip_nonnumeric_source_marks(text) == text

    def test_prose_sentence_drops_mark(self):
        from src.core.citations import strip_nonnumeric_source_marks

        text = "제도의 취지는 탄소누출 방지에 있음(출처 17)"
        assert strip_nonnumeric_source_marks(text) == "제도의 취지는 탄소누출 방지에 있음"

    def test_sentence_boundary_isolates_judgement(self):
        from src.core.citations import strip_nonnumeric_source_marks

        text = "규모는 428개사임(출처 13).\n취지는 전환 유도에 있음(출처 13)"
        out = strip_nonnumeric_source_marks(text)
        assert "428개사임(출처 13)" in out
        assert "전환 유도에 있음(출처 13)" not in out

    def test_direct_quote_untouched(self):
        from src.core.citations import strip_nonnumeric_source_marks

        text = "원문 인용이다 [3]"
        assert strip_nonnumeric_source_marks(text) == text
