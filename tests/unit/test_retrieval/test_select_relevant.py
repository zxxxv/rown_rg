"""근거 개수를 분포로 정하는 선택 — 고정 top_k가 확신 구간을 넘던 문제.

실측(2026-08-12, 14절): 절당 리랭커 점수 0.5 이상이 중앙 15개인데 32개를 채우느라
평균이 0.469까지 내려갔고 하위 10%는 0.011이었다. 절마다 쓸 만한 근거 수가 0~27개로
크게 달라 고정 개수는 어느 쪽으로든 틀린다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

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


def _hits_from(sources: list[UUID]) -> list[SearchHit]:
    """출처를 지정한 히트 - 점수는 순위대로 내려간다(1위 0.9, 하한 위)."""
    return [
        SearchHit(
            chunk_id=uuid4(),
            source_id=src,
            content=f"근거 {i}",
            score=0.9 - i * 0.001,
            metadata={},
            chunk_index=i,
            score_source="reranker",
        )
        for i, src in enumerate(sources)
    ]


@pytest.fixture(autouse=True)
def _fixed_ratio():
    old = settings.retrieval_score_ratio
    old_cap = settings.retrieval_source_cap_ratio
    settings.retrieval_score_ratio = 0.10
    settings.retrieval_source_cap_ratio = 0.25
    yield
    settings.retrieval_score_ratio = old
    settings.retrieval_source_cap_ratio = old_cap


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


# --- 출처별 상한 (2026-08-14: 자료 1건이 전체 인용의 28%를 먹던 문제) ---


def test_큰_자료_하나가_절을_도배하지_못한다() -> None:
    """탄소규제 런 재현 - 청크 100개짜리 PDF가 상위를 싹쓸이한 풀.

    고를 자료가 충분하면(여기선 8건) 상한이 그대로 걸려 캡 24의 25%(=6)까지만 간다.
    """
    big = uuid4()
    others = [uuid4() for _ in range(7)]
    ranked = _hits_from([big] * 100 + [s for s in others for _ in range(5)])
    kept = select_relevant(ranked, cap=24)
    assert sum(1 for h in kept if h.source_id == big) == 6
    assert len(kept) == 24  # 나머지 슬롯은 다른 자료로 채워진다
    assert len({h.source_id for h in kept}) >= 5


def test_작은_자료가_상위에_자리를_얻는다() -> None:
    """4청크짜리 자료가 큰 PDF 뒤에 있어도 슬롯이 남아 뽑힌다."""
    big, small = uuid4(), uuid4()
    ranked = _hits_from([big] * 30 + [small] * 4)
    kept = select_relevant(ranked, cap=24)
    assert sum(1 for h in kept if h.source_id == small) == 4


def test_자료가_적으면_상한이_근거를_굶기지_않는다() -> None:
    """업로드 2건짜리 프로젝트 - 상한대로면 12개지만 되채워 캡을 채운다.

    분량 목표가 근거 수에 비례하므로(writer_context.scale_for_evidence) 여기서 굶기면
    다양화의 대가로 절이 짧아진다. 고를 게 없을 때는 다양성을 살 수 없다.
    """
    a, b = uuid4(), uuid4()
    ranked = _hits_from([a] * 20 + [b] * 20)
    kept = select_relevant(ranked, cap=24)
    assert len(kept) == 24
    # 그래도 상한 없이 뽑던 때(a가 20개 독식)보다는 균형이 낫다.
    assert sum(1 for h in kept if h.source_id == b) >= 6


def test_자료가_하나뿐이어도_캡까지_간다() -> None:
    only = uuid4()
    assert len(select_relevant(_hits_from([only] * 30), cap=24)) == 24


def test_되채운_뒤에도_점수_순서가_유지된다() -> None:
    a, b = uuid4(), uuid4()
    ranked = _hits_from([a] * 10 + [b] * 2)
    kept = select_relevant(ranked, cap=12)
    assert [h.score for h in kept] == sorted((h.score for h in kept), reverse=True)


def test_상한의_하한은_지켜진다() -> None:
    """캡이 작아 비율상 1개여도 한 자료에서 최소 3개는 쓸 수 있다(맥락 유지)."""
    big, other = uuid4(), uuid4()
    ranked = _hits_from([big] * 10 + [other] * 10)
    kept = select_relevant(ranked, cap=4)  # 4 * 0.25 = 1 → 하한 3
    assert sum(1 for h in kept if h.source_id == big) == 3
