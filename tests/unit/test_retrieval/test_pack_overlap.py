"""절쌍 근거팩 겹침 실측(2026-09-02) - 질의 문자열 중복 경고가 못 보는 병리 고정.

철강 실사고: 1.1↔5.1은 질의가 달랐지만 같은 K-스틸법 자료를 받아 축자 재탕 14건.
7차 실측: 쌍둥이 절 자카드 0.59~0.62. 리허설 결과로 겹침을 재서 게이트에 올린다.
"""

from __future__ import annotations

from src.services.retrieval.rehearsal import (
    PACK_OVERLAP_THRESHOLD,
    overlap_pairs_from_packs,
)


def test_임계_이상_겹침만_자카드_내림차순으로() -> None:
    packs = {
        "1.1": {"a", "b", "c", "d"},
        "5.1": {"a", "b", "c", "x"},  # 자카드 3/5 = 0.6
        "2.1": {"p", "q"},  # 무겹침
    }
    out = overlap_pairs_from_packs(packs)
    assert len(out) == 1
    assert out[0]["sections"] == ["1.1", "5.1"]
    assert out[0]["jaccard"] == 0.6
    assert set(out[0]["shared_chunk_ids"]) == {"a", "b", "c"}


def test_임계_미만은_침묵한다() -> None:
    packs = {"1.1": {"a", "b", "c", "d", "e"}, "3.2": {"a", "x", "y", "z", "w"}}
    assert overlap_pairs_from_packs(packs) == []
    assert PACK_OVERLAP_THRESHOLD == 0.45  # 7차 실측(0.59~0.62 재탕) 아래 여유선


def test_빈_팩은_계산에서_빠진다() -> None:
    assert overlap_pairs_from_packs({"1.1": set(), "2.1": {"a"}}) == []
