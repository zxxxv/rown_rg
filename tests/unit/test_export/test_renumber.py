"""인용 전역 번호화 — 로컬 [n]→전역 [g] 재작성의 결정성 검증 (순수 함수)."""

from __future__ import annotations

from uuid import uuid4

from src.services.sections.renumber import renumber_content


class TestRenumberContent:
    def test_locals_rewritten_to_globals(self):
        c1, c2 = uuid4(), uuid4()
        # 로컬 등장 순서: [2](→c1), [1](→c2). 전역: c1=7, c2=3.
        content = "핵심 수치임 [2]. 반대 근거도 있음 [1]. 재인용 [2]."
        out = renumber_content(content, [c1, c2], {c1: 7, c2: 3})
        assert out == "핵심 수치임 [7]. 반대 근거도 있음 [3]. 재인용 [7]."

    def test_same_source_chunks_share_global_number(self):
        c1, c2 = uuid4(), uuid4()
        content = "첫 근거 [1] 그리고 둘째 근거 [2]"
        out = renumber_content(content, [c1, c2], {c1: 5, c2: 5})  # 같은 자료의 두 청크
        assert out == "첫 근거 [5] 그리고 둘째 근거 [5]"

    def test_unmapped_marker_dropped_with_leading_space(self):
        c1 = uuid4()
        # [9]는 cited 목록 범위 밖(환각) — 앞 공백째 제거되고 문장은 자연스럽게 남는다.
        content = "근거 있음 [1]. 근거 없음 [9]."
        out = renumber_content(content, [c1], {c1: 2})
        assert out == "근거 있음 [2]. 근거 없음."

    def test_newline_not_swallowed_on_drop(self):
        c1 = uuid4()
        content = "첫 줄 [1]\n[7] 둘째 줄"  # [7] 미매핑 — 개행은 보존돼야 한다
        out = renumber_content(content, [c1], {c1: 1})
        assert out.startswith("첫 줄 [1]\n")
        assert "[7]" not in out and "둘째 줄" in out

    def test_no_markers_returns_content_unchanged(self):
        assert renumber_content("인용 없는 본문", [], {}) == "인용 없는 본문"


class TestLostEvidenceParagraphs:
    """마커가 지워진 자리를 남긴다 — 지워진 마커는 흔적이 없어 나중에는 알 수 없다."""

    def test_only_paragraphs_that_actually_lost_markers(self):
        from src.services.sections.renumber import lost_evidence_paragraphs

        before = "첫 문단 [1].\n\n둘째 문단 [1][2].\n\n셋째 문단 [2]."
        after = "첫 문단.\n\n둘째 문단 [1].\n\n셋째 문단 [1]."
        lost = lost_evidence_paragraphs(before, after)
        # 셋째는 번호만 당겨졌다 — 근거를 잃지 않았으므로 짚으면 거짓말이다.
        assert [p["text"] for p in lost] == ["첫 문단.", "둘째 문단 [1]."]
        assert [p["n_markers"] for p in lost] == [1, 1]

    def test_paragraph_count_mismatch_records_nothing(self):
        """다른 편집이 겹쳐 자리가 어긋나면 아무것도 기록하지 않는다.

        틀린 자리를 짚느니 안 짚는 게 낫다 — 사람이 멀쩡한 문단을 다시 쓰게 된다.
        """
        from src.services.sections.renumber import lost_evidence_paragraphs

        assert lost_evidence_paragraphs("가 [1].\n\n나 [1].", "가.") == []

    def test_no_change_records_nothing(self):
        from src.services.sections.renumber import lost_evidence_paragraphs

        assert lost_evidence_paragraphs("가 [1].", "가 [1].") == []
