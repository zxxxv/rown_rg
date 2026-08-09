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
