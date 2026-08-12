"""근거 개수를 분포로 정하는 선택 — 고정 top_k가 확신 구간을 넘던 문제.

실측(2026-08-12, 14절): 절당 리랭커 점수 0.5 이상이 중앙 15개인데 32개를 채우느라
평균이 0.469까지 내려갔고 하위 10%는 0.011이었다. 절마다 쓸 만한 근거 수가 0~27개로
크게 달라 고정 개수는 어느 쪽으로든 틀린다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.config import settings
from src.services.retrieval._reranking import select_relevant
from src.services.retrieval.base import SearchHit


def _hits(*scores: float) -> list[SearchHit]:
    return [
        SearchHit(
            chunk_id=uuid4(),
            source_id=uuid4(),
            content=f"근거 {i}",
            score=s,
            metadata={},
            chunk_index=i,
            score_source="reranker",
        )
        for i, s in enumerate(scores)
    ]


@pytest.fixture(autouse=True)
def _fixed_ratio():
    old = settings.retrieval_score_ratio
    settings.retrieval_score_ratio = 0.10
    yield
    settings.retrieval_score_ratio = old


def test_풍부한_절은_캡까지_가져간다() -> None:
    ranked = _hits(*[0.9 - i * 0.01 for i in range(40)])  # 전부 하한 위
    assert len(select_relevant(ranked, cap=24)) == 24


def test_빈약한_절은_있는_만큼만_가져간다() -> None:
    """1위가 0.9면 하한은 0.09 - 그 아래는 근거로 안 싣는다."""
    ranked = _hits(0.9, 0.8, 0.5, 0.05, 0.01, 0.001)
    assert len(select_relevant(ranked, cap=24)) == 3


def test_1위가_낮아도_비율은_1위_기준이다() -> None:
    """수집이 빈 절(1위 0.45)에서도 상대적으로 쓸 만한 것은 남긴다."""
    ranked = _hits(0.45, 0.40, 0.20, 0.03, 0.001)
    assert len(select_relevant(ranked, cap=24)) == 3


def test_전부_하한_아래여도_최소_개수는_남긴다() -> None:
    """근거 0개면 그 절은 아예 못 쓴다 - 수집 공백은 design_coverage가 따로 알린다."""
    ranked = _hits(0.0, 0.0, 0.0, 0.0)
    assert len(select_relevant(ranked, cap=24)) == 3


def test_캡이_최소보다_작으면_캡을_지킨다() -> None:
    assert len(select_relevant(_hits(0.0, 0.0, 0.0), cap=2)) == 2


def test_빈_입력() -> None:
    assert select_relevant([], cap=24) == []


def test_점수_순서를_지킨다() -> None:
    ranked = _hits(0.9, 0.7, 0.5)
    assert [h.score for h in select_relevant(ranked, cap=24)] == [0.9, 0.7, 0.5]
