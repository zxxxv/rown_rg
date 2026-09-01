"""MMR 재정렬 — 다양성을 사되 관련성을 잃지 않는지.

실측 배경(2026-08-12, 14절 보고서): 시장조사 보고서의 총론 문단 몇 개가 절마다 상위를
차지했고, 그 사이 국내 실측 수치가 든 청크는 한 번도 안 뽑혔다. 리랭커는 질의와
표면적으로 가까운 걸 올리므로 비슷한 문단이 여럿이면 자리를 나눠 갖는다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.config import settings
from src.services.retrieval._reranking import _mmr
from src.services.retrieval.base import SearchHit

_MARKET = (
    "글로벌 숏폼 시장은 2021년 432억 달러에서 2026년 1,350억 달러로 연평균 25.6% "
    "성장할 것으로 전망되며, 플랫폼 경쟁이 심화되고 있다."
)
_MARKET2 = (
    "글로벌 숏폼 시장은 2021년 432억 달러에서 2026년 1,350억 달러로 연평균 25.6% "
    "성장할 전망이고, 플랫폼 간 경쟁도 치열해지고 있다."
)
_DOMESTIC = (
    "네이버 클립은 지난해 12월 기준 재생 수가 전년 동기 대비 7배로 늘었고, "
    "채널 수와 콘텐츠 편수도 함께 증가했다."
)


def _hit(content: str, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        content=content,
        score=score,
        metadata={},
        chunk_index=0,
        score_source="reranker",
    )


@pytest.fixture
def mmr_on():
    old_enabled, old_lambda = settings.mmr_enabled, settings.mmr_lambda
    settings.mmr_enabled, settings.mmr_lambda = True, 0.75
    yield
    settings.mmr_enabled, settings.mmr_lambda = old_enabled, old_lambda


def test_거의_같은_문단이_자리를_나눠_갖지_않는다(mmr_on) -> None:
    """점수만 보면 총론 두 개가 1·2위지만, 두 번째는 새 정보가 없다."""
    ranked = [_hit(_MARKET, 0.95), _hit(_MARKET2, 0.93), _hit(_DOMESTIC, 0.70)]
    picked = _mmr(ranked, top_k=2)
    contents = [h.content for h in picked]
    assert _MARKET in contents
    assert _DOMESTIC in contents, "중복 총론 대신 새 내용이 들어와야 한다"


def test_첫째는_언제나_최고점이다(mmr_on) -> None:
    ranked = [_hit(_MARKET, 0.95), _hit(_MARKET2, 0.93), _hit(_DOMESTIC, 0.70)]
    assert _mmr(ranked, top_k=3)[0].content == _MARKET


def test_후보가_적으면_그대로_돌려준다(mmr_on) -> None:
    ranked = [_hit(_MARKET, 0.9), _hit(_DOMESTIC, 0.8)]
    assert [h.content for h in _mmr(ranked, top_k=5)] == [_MARKET, _DOMESTIC]


def test_서로_다른_내용은_점수_순서를_지킨다(mmr_on) -> None:
    """다양화가 관련성을 뒤집으면 안 된다 - 겹치지 않는 후보끼리는 원래 순위 그대로."""
    a = _hit("반도체 소부장 국산화율은 30% 수준에 머물러 있다.", 0.9)
    b = _hit("숏폼 이용자는 하루 평균 78분을 시청한다.", 0.8)
    c = _hit("정부는 2026년 예산에 1조 원을 편성했다.", 0.7)
    assert [h.content for h in _mmr([a, b, c], top_k=3)] == [a.content, b.content, c.content]


def test_lambda_1이면_원래_동작과_같다() -> None:
    old = settings.mmr_lambda
    settings.mmr_lambda = 1.0
    try:
        ranked = [_hit(_MARKET, 0.95), _hit(_MARKET2, 0.93), _hit(_DOMESTIC, 0.70)]
        assert [h.content for h in _mmr(ranked, top_k=2)] == [_MARKET, _MARKET2]
    finally:
        settings.mmr_lambda = old
