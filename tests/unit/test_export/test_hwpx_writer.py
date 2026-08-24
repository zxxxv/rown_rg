"""hwpx_writer의 순수 계산 함수 검증 — 렌더 결과가 아니라 서식 수치를 고정한다."""

from __future__ import annotations

from src.export.hwpx_writer import (
    BODY_ALIGNMENT,
    BODY_SIZE_PT,
    CELL_LINE_SPACING_PERCENT,
    CELL_MARGIN_SIDE_HWP,
    CELL_MARGIN_VERT_HWP,
    LINE_SPACING_PERCENT,
    MARGIN_MM,
    MIN_SPACE_RATIO_PERCENT,
    OUTLINE_STEP_PT,
    _hanging_indent_mm,
    _pad_marker,
    citation_runs,
)

# 반각 한 칸의 폭(mm) — 본문 글자 크기의 절반. 기대값을 손으로 적지 않고 같은 근거로 세운다.
_HALF = BODY_SIZE_PT * (25.4 / 72) / 2


class TestHangingIndent:
    """개조식 항목이 두 줄로 넘어갈 때 둘째 줄을 본문 글머리에 맞추기 위한 내어쓰기 폭."""

    def test_wide_marker_counts_as_two_half_widths(self):
        # "ㅇ "= 전각 마커(2) + 공백(1) = 반각 3칸.
        assert _hanging_indent_mm("ㅇ 시장 규모가 확대됨") == 3 * _HALF

    def test_box_marker_same_as_other_wide_markers(self):
        assert _hanging_indent_mm("□ 추진 배경") == 3 * _HALF

    def test_narrow_marker_uses_the_same_slot(self):
        # 좁은 마커도 같은 칸을 쓴다 — 칸 폭이 마커마다 다르면 본문 시작점이 계단을
        # 거슬러 역전된다(2026-08-24 실사고: ㅇ 24.5pt 아래 '-'가 23.0pt에 섰다).
        assert _hanging_indent_mm("- 세부 항목임") == 3 * _HALF
        assert _hanging_indent_mm("* 보충 설명임") == 3 * _HALF

    def test_narrow_marker_padded_to_slot(self):
        # 칸을 공백으로 채워야 첫 줄 본문과 줄바꿈된 줄이 같은 자리에서 시작한다.
        assert _pad_marker("- 세부 항목임") == "-  세부 항목임"
        assert _pad_marker("ㅇ 중주제임") == "ㅇ 중주제임"  # 전각 마커는 이미 3칸
        assert _pad_marker("서술 문단임") == "서술 문단임"

    def test_plain_paragraph_has_no_hanging_indent(self):
        # 마커 없는 서술 문단은 첫 줄을 당길 이유가 없다.
        assert _hanging_indent_mm("서술형 문단이라 마커가 없음") == 0.0

    def test_marker_without_following_space_ignored(self):
        # "-3.2%p 감소"처럼 마커가 아니라 부호로 시작하는 줄을 내어쓰면 안 된다.
        assert _hanging_indent_mm("-3.2%p 감소함") == 0.0

    def test_too_short_text_ignored(self):
        assert _hanging_indent_mm("ㅇ") == 0.0
        assert _hanging_indent_mm("") == 0.0


class TestOutlineStep:
    """개조식 계단 — 글머리 첫 줄이 (수준+1)×4pt: □4·ㅇ8·-12 (2026-08-24 지시)."""

    def test_step_is_four_points(self):
        assert OUTLINE_STEP_PT == 4.0

    def test_ladder_is_tighter_than_marker_width(self):
        # 한 단(4pt)이 마커 폭보다 좁다 — 사다리가 얕게 당겨진 모양의 근거.
        assert OUTLINE_STEP_PT * (25.4 / 72) < _hanging_indent_mm("□ 대주제")


class TestCompanyStyle:
    """실납품 실측·사용자 확정 서식(2026-08-24) — 회귀하면 납품 모양이 돌아간다."""

    def test_page_margins_match_delivered_samples(self):
        # 실납품 2종 실측 일치: 좌우 20·상하 15 (종전 좌30·상하20은 한글 기본값 잔재).
        assert MARGIN_MM == {"top": 15.0, "bottom": 15.0, "left": 20.0, "right": 20.0}

    def test_body_spacing_160_cells_130(self):
        # 본문은 160%(130%로 내렸다가 눈으로 보고 되돌림), 표 안은 좁게 130%.
        assert LINE_SPACING_PERCENT == 160
        assert CELL_LINE_SPACING_PERCENT == 130

    def test_body_alignment_and_min_space_match_samples(self):
        # 실납품 6종 전부 양쪽 정렬 주력(60~94%)이고, 알키미스트 본문 83%가 최소
        # 공백 25%다. 벌어짐의 병인은 정렬이 아니라 최소 공백 0%였다(2026-08-24).
        assert BODY_ALIGNMENT == "JUSTIFY"
        assert MIN_SPACE_RATIO_PERCENT == 25

    def test_body_size_follows_delivered_samples(self):
        # 실납품 본문 13pt(KoPub바탕체) 실측 → 종전 11pt에서 12pt로. 글꼴 체감 차이가
        # 있어 13pt는 샘플 비교 후 결정(2026-08-24).
        assert BODY_SIZE_PT == 12


class TestCitationRuns:
    """본문 인용 마커는 번호만 위첨자로 올린다(2026-08-24 지시).

    실납품 보고서는 본문에 인라인 출처를 아예 쓰지 않고 각주로 내린다(알키미스트
    실측: 인라인 0건·각주 6건). 괄호째 본문에 박히면 문장 흐름이 끊긴다.
    """

    def test_marker_becomes_superscript_number(self):
        assert citation_runs("국내 생산이 늘었음(출처 13, 25)") == [
            ("국내 생산이 늘었음", False),
            ("13,25", True),
        ]

    def test_plain_text_stays_one_run(self):
        assert citation_runs("마커가 없는 문장임") == [("마커가 없는 문장임", False)]

    def test_marker_in_the_middle_keeps_both_sides(self):
        assert citation_runs("앞부분 (출처 3) 뒷부분") == [
            ("앞부분", False),
            ("3", True),
            (" 뒷부분", False),
        ]

    def test_non_citation_parens_untouched(self):
        # 괄호 안이 숫자 목록이어도 '출처/자료' 라벨이 없으면 인용이 아니다.
        assert citation_runs("총 3개 부문(1, 2, 3)을 다룸") == [
            ("총 3개 부문(1, 2, 3)을 다룸", False)
        ]


class TestCellMargins:
    """표 셀 안 여백 — 한컴이 표를 새로 넣을 때 쓰는 값과 같아야 한다(실납품 샘플 실측)."""

    def test_side_margin_is_hancom_default(self):
        assert CELL_MARGIN_SIDE_HWP == 510  # 1.8mm

    def test_vertical_margin_is_hancom_default(self):
        assert CELL_MARGIN_VERT_HWP == 141  # 0.5mm

    def test_margins_are_not_zero(self):
        # 0이면 글자가 괘선에 붙는다 — python-hwpx 기본값이 그래서 되돌려 준다.
        assert CELL_MARGIN_SIDE_HWP > 0
        assert CELL_MARGIN_VERT_HWP > 0
