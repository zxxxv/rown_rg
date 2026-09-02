"""정본 배정 병합의 중복 방지(2026-09-03) - 재실행마다 같은 배정이 6중으로 쌓이던 결함."""

from __future__ import annotations

from src.services.generation.source_canon import _dedup_join, merge_into_notes


def test_같은_배정을_두_번_병합해도_한_번만_남는다() -> None:
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


def test_새_항목은_기존_뒤에_덧붙는다() -> None:
    assert _dedup_join("가 · 나", "나 · 다") == "가 · 나 · 다"
    assert _dedup_join("", "가 · 가") == "가"
