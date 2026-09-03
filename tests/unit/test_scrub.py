"""조립 세정 — 작성 잔재의 결정적 제거 (2026-08-15 검증런 실측 잔재가 회귀셋).

정밀도 원칙: 좁은 템플릿만 지우고, 정상 산문·정식 문법은 보존한다.
"""

from __future__ import annotations

from src.services.sections.scrub import scrub_leftovers


class TestSourceMemoRemoval:
    def test_exclusion_memos_removed(self):
        # 실측 3.3의 8건 형태들.
        cases = [
            "ㅇ 관세 수입은 34억 달러로 추정됨 (출처 17 제외)",
            "ㅇ 부담금은 149억 달러 규모임 (출처 28 제외 → 근거 미사용)",
            "ㅇ 제도 평가가 이어짐 (출처 21은 사용 불가 — 해당 서술 생략)",
            "ㅇ 시장 반응은 긍정적임 (출처 27은 이 파트 대상이 아니므로 생략)",
        ]
        for body in cases:
            cleaned, notes = scrub_leftovers(body)
            assert "출처" not in cleaned, body
            assert notes, body

    def test_reassign_memo_keeps_target_marker(self):
        body = "ㅇ 대상 품목 커버리지가 가장 넓음 (출처 8, 19 중 8만 사용 → (출처 22))"
        cleaned, notes = scrub_leftovers(body)
        assert cleaned.endswith("(출처 22)")
        assert "8만 사용" not in cleaned
        assert any("재지정" in n for n in notes)

    def test_legit_marker_and_prose_preserved(self):
        # 정상 마커와 '제외'를 말하는 산문은 지우면 안 된다(정밀도 가드).
        body = "ㅇ 판재류는 대상에서 제외된 품목임 (출처 22). 상세는 (출처 12에서 제외된 품목) 참조"
        cleaned, notes = scrub_leftovers(body)
        assert "(출처 22)" in cleaned
        assert "(출처 12에서 제외된 품목)" in cleaned


class TestMarkerAndCalloutRepair:
    def test_corrupted_marker_repaired(self):
        cleaned, notes = scrub_leftovers("ㅇ 부담이 확대됨 (출превод처 25)")
        assert "(출처 25)" in cleaned
        assert any("오염" in n for n in notes)

    def test_raw_callout_tags_become_fences(self):
        body = "<callout(warn)>\n※ 근거 한계 고지\n</callout>"
        cleaned, _ = scrub_leftovers(body)
        assert cleaned == "::: callout(warn)\n※ 근거 한계 고지\n:::"
        body2 = '<callout type="info">\n안내\n</callout>'
        cleaned2, _ = scrub_leftovers(body2)
        assert cleaned2.startswith("::: callout(info)")

    def test_canonical_fence_untouched(self):
        body = "::: callout(warn)\n정식 고지\n:::"
        cleaned, notes = scrub_leftovers(body)
        assert cleaned == body
        assert notes == []


class TestInternalTermSwap:
    def test_bon_part_swapped_with_particles(self):
        body = "본 파트에서는 제도 구조를 정리함. 본 파트는 개요 역할을 함."
        cleaned, notes = scrub_leftovers(body)
        assert "이 절에서는" in cleaned and "이 절은" in cleaned
        assert "본 파트" not in cleaned
        assert any("본 파트" in n for n in notes)

    def test_clean_content_returns_no_notes(self):
        body = "ㅇ 국내 기업의 대응 수준은 개선되는 흐름을 보이고 있음 (출처 3)"
        cleaned, notes = scrub_leftovers(body)
        assert cleaned == body and notes == []


class TestTableForbiddenCells:
    """표 금지 셀(2026-08-21 v6 결함 세대교체 3번) - 생성단 세정.

    렌더 층('-' 치환)만 있으면 웹 미리보기·검사가 금지값을 그대로 본다. 조립 세정이
    정본이고 렌더는 세정 밖 경로용 이중 방어로 남는다(값 집합은 EMPTY_CELL_VALUES 공유).
    """

    def test_cells_normalized_prose_untouched(self) -> None:
        body = (
            "| 항목 | 2024 | 비고 |\n"
            "| --- | --- | --- |\n"
            "| 매출 | 120억 | 자료 없음 |\n"
            "| 수출 | N/A | 상동 |\n\n"
            "산문에서 자료 없음이라는 표현은 문장의 일부라 남아야 한다.\n"
        )
        out, notes = scrub_leftovers(body)
        assert "| 매출 | 120억 | - |" in out
        assert "자료 없음이라는 표현" in out  # 산문은 안 건드린다
        assert any("표 금지 셀 정리 3건" in n for n in notes)

    def test_partial_match_cell_kept(self) -> None:
        # 셀 전체가 금지값일 때만 - "자료 없음(추정치 활용)"은 정보가 있는 셀이다.
        body = "| 지표 | 자료 없음(추정치 활용) |\n"
        out, notes = scrub_leftovers(body)
        assert out == body
        assert not any("표 금지 셀" in n for n in notes)

    def test_trailing_newline_preserved(self) -> None:
        body = "| a | N/A |\n"
        out, _ = scrub_leftovers(body)
        assert out.endswith("\n") and "| - |" in out


def test_해당없음_배정메모_신형(self=None) -> None:
    """v6 이관본 4.2 실측(2026-08-27): "(출처 26에 해당 없음 — 아래 항목 참조)"가
    본문에 그대로 실려 있었다 - 제외/사용불가/생략만 알던 패턴의 사각."""
    body = "ㅇ 자산 연한 기준은 최소 85%가 공급되어야 함(출처 26에 해당 없음 — 아래 항목 참조)\n"
    out, notes = scrub_leftovers(body)
    assert "해당 없음" not in out
    assert any("배정 메모" in n for n in notes)
    # 산문의 '해당 없음'은 문장의 일부다 - 괄호 밖이면 안 걷는다.
    prose = "분석 결과 해당 없음으로 판정된 항목(출처 12)은 제외했다.\n"
    assert scrub_leftovers(prose)[0] == prose


class TestReplaceMemo:
    """교체 메모(2026-08-31 철강 4.3 재작성 실측) - 최종 번호만 남긴다."""

    def test_교체_메모는_최종_번호만_남긴다(self):
        from src.services.sections.scrub import scrub_leftovers

        out, notes = scrub_leftovers("지역 분포는 비대칭 구조로 나타남(출처 56 대신 출처 106)")
        assert out == "지역 분포는 비대칭 구조로 나타남 (출처 106)"
        assert any("교체 메모" in n for n in notes)

    def test_꼬리_산문이_붙은_긴_변형은_남긴다(self):
        # 의미 판단이 필요한 꼴은 세정하지 않고 검출기 경고 몫으로 둔다.
        from src.services.sections.scrub import scrub_leftovers

        text = "단계에 있음(출처 11 대신 출처 12 기준 — 유럽이 선도하고 있음)(출처 11)"
        out, _notes = scrub_leftovers(text)
        assert "대신" in out


class TestMarkerLabelCanon:
    """세정 첫 단계의 라벨 정본화(2026-09-03) - 변형 라벨이 재번호를 지나치던 구멍."""

    def test_변형_라벨이_세정에서_정본화된다(self) -> None:
        out, notes = scrub_leftovers("확대됐다 (근거 3). 성장했다 (자료 7).")
        assert out == "확대됐다 (출처 3). 성장했다 (출처 7)."
        assert any("라벨 정본화" in n for n in notes)

    def test_정본_라벨만_있으면_손대지_않는다(self) -> None:
        text = "확대됐다 (출처 3)."
        out, notes = scrub_leftovers(text)
        assert out == text
        assert notes == []
