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
