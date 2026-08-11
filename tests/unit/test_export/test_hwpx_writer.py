"""hwpx_writer의 순수 계산 함수 검증 — 렌더 결과가 아니라 서식 수치를 고정한다."""

from __future__ import annotations

from src.export.hwpx_writer import BODY_SIZE_PT, _hanging_indent_mm

# 반각 한 칸의 폭(mm) — 본문 글자 크기의 절반. 기대값을 손으로 적지 않고 같은 근거로 세운다.
_HALF = BODY_SIZE_PT * (25.4 / 72) / 2


class TestHangingIndent:
    """개조식 항목이 두 줄로 넘어갈 때 둘째 줄을 본문 글머리에 맞추기 위한 내어쓰기 폭."""

    def test_wide_marker_counts_as_two_half_widths(self):
        # "ㅇ "= 전각 마커(2) + 공백(1) = 반각 3칸.
        assert _hanging_indent_mm("ㅇ 시장 규모가 확대됨") == 3 * _HALF

    def test_box_marker_same_as_other_wide_markers(self):
        assert _hanging_indent_mm("□ 추진 배경") == 3 * _HALF

    def test_narrow_marker_is_narrower(self):
        # "- "= 반각 마커(1) + 공백(1) = 반각 2칸. 전각 마커보다 좁게 내어쓴다.
        assert _hanging_indent_mm("- 세부 항목임") == 2 * _HALF
        assert _hanging_indent_mm("* 보충 설명임") == 2 * _HALF

    def test_plain_paragraph_has_no_hanging_indent(self):
        # 마커 없는 서술 문단은 첫 줄을 당길 이유가 없다.
        assert _hanging_indent_mm("서술형 문단이라 마커가 없음") == 0.0

    def test_marker_without_following_space_ignored(self):
        # "-3.2%p 감소"처럼 마커가 아니라 부호로 시작하는 줄을 내어쓰면 안 된다.
        assert _hanging_indent_mm("-3.2%p 감소함") == 0.0

    def test_too_short_text_ignored(self):
        assert _hanging_indent_mm("ㅇ") == 0.0
        assert _hanging_indent_mm("") == 0.0
