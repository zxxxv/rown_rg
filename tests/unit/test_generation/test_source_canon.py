"""정본 배정(source_canon) — 파싱·병합·실패 격리.

원칙 검증: 유령 절 배정은 버리고, 병합은 runner 계약(owns/foreign_topics)과 같은
모양을 만들며, 어떤 실패든 0건으로 끝난다(작성은 1층 배정만으로 계속).
"""

from __future__ import annotations

from src.services.generation.source_canon import (
    MAX_CANON_TOPICS,
    _dedup_join,
    merge_into_notes,
    parse_assignments,
)

_KNOWN = {"1.1", "1.3", "4.3"}


class TestParse:
    def test_keeps_valid_drops_ghost_and_dupes(self) -> None:
        raw = {
            "source_canon": [
                {"topic": "『RE100 실태조사』의 업종별 수치", "owner": "1.3"},
                {"topic": "『RE100 실태조사』의  업종별   수치", "owner": "4.3"},  # 중복 토픽
                {"topic": "『없는절 배정』", "owner": "9.9"},  # 유령 절
                {"topic": "", "owner": "1.1"},  # 빈 토픽
                {"topic": "『K-RE100 안내서』의 이행수단 기준", "owner": "4.3"},
            ]
        }
        out = parse_assignments(raw, _KNOWN)
        assert [a["owner"] for a in out] == ["1.3", "4.3"]

    def test_cap(self) -> None:
        raw = {
            "source_canon": [
                {"topic": f"자료{i}의 수치", "owner": "1.1"} for i in range(MAX_CANON_TOPICS + 5)
            ]
        }
        assert len(parse_assignments(raw, _KNOWN)) == MAX_CANON_TOPICS

    def test_non_dict_items(self) -> None:
        assert parse_assignments({"source_canon": ["문자열", 3, None]}, _KNOWN) == []


class TestMerge:
    LABEL_TO_SID = {"1.1": "sid-11", "1.3": "sid-13", "4.3": "sid-43"}

    def test_owner_gets_owns_others_get_foreign(self) -> None:
        notes = {"sid-13": {"goal": "실태 진단", "owns": "구조 수준 담당"}}
        assignments = [{"topic": "『실태조사』의 수치", "owner": "1.3"}]
        merged = merge_into_notes(notes, assignments, self.LABEL_TO_SID)
        assert "구조 수준 담당 · 『실태조사』의 수치" == merged["sid-13"]["owns"]
        assert "『실태조사』의 수치(1.3절 소관)" in merged["sid-11"]["foreign_topics"]
        assert "『실태조사』의 수치(1.3절 소관)" in merged["sid-43"]["foreign_topics"]
        # 기존 goal은 보존된다
        assert merged["sid-13"]["goal"] == "실태 진단"

    def test_original_not_mutated(self) -> None:
        notes = {"sid-13": {"owns": "기존"}}
        merge_into_notes(notes, [{"topic": "T", "owner": "1.3"}], self.LABEL_TO_SID)
        assert notes["sid-13"]["owns"] == "기존"

    def test_empty_assignments_keeps_notes(self) -> None:
        notes = {"sid-13": {"goal": "유지"}}
        merged = merge_into_notes(notes, [], self.LABEL_TO_SID)
        assert merged == {"sid-13": {"goal": "유지"}}


class TestDedupJoin:
    """정본 배정 병합의 중복 방지(2026-09-03) - 재실행마다 같은 배정이 6중으로 쌓이던 결함."""

    def test_같은_배정을_두_번_병합해도_한_번만_남는다(self) -> None:
        assignments = [
            {"topic": "K-스틸법 시행령 주요 조항", "owner": "1.1"},
            {"topic": "배출권거래제 할당 기준", "owner": "1.1"},
        ]
        label_to_sid = {"1.1": "sid-a", "5.1": "sid-b"}
        once = merge_into_notes({}, assignments, label_to_sid)
        twice = merge_into_notes(once, assignments, label_to_sid)
        assert twice["sid-a"]["owns"] == once["sid-a"]["owns"]
        assert twice["sid-a"]["owns"].count("K-스틸법") == 1
        assert twice["sid-b"]["foreign_topics"].count("K-스틸법") == 1

    def test_새_항목은_기존_뒤에_덧붙는다(self) -> None:
        assert _dedup_join("가 · 나", "나 · 다") == "가 · 나 · 다"
        assert _dedup_join("", "가 · 가") == "가"
