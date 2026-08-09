"""HybridSearchClient tests — RRF fusion with mocked semantic + keyword clients."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.services.retrieval.base import SearchHit
from src.services.retrieval.hybrid import HybridSearchClient

pytestmark = pytest.mark.asyncio


def _hit(chunk_id: UUID, content: str, score: float, source: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        content=content,
        source_id=uuid4(),
        chunk_index=0,
        metadata={},
        score=score,
        score_source=source,
    )


def _make_clients(
    semantic_hits: list[SearchHit] | Exception, keyword_hits: list[SearchHit] | Exception
) -> tuple[MagicMock, MagicMock]:
    semantic = MagicMock()
    if isinstance(semantic_hits, Exception):
        semantic.search = AsyncMock(side_effect=semantic_hits)
    else:
        semantic.search = AsyncMock(return_value=semantic_hits)
    keyword = MagicMock()
    if isinstance(keyword_hits, Exception):
        keyword.search = AsyncMock(side_effect=keyword_hits)
    else:
        keyword.search = AsyncMock(return_value=keyword_hits)
    return semantic, keyword


class TestHybridSearchPositive:
    async def test_both_called_in_parallel(self):
        sem, kw = _make_clients(semantic_hits=[], keyword_hits=[])
        client = HybridSearchClient(sem, kw)
        await client.search("q", uuid4())
        sem.search.assert_awaited_once()
        kw.search.assert_awaited_once()

    async def test_dedupe_combines_overlapping_chunk_id(self):
        chunk_x = uuid4()
        chunk_y = uuid4()
        chunk_z = uuid4()
        # X는 양쪽에서 잡힘 — RRF 합산으로 최상위가 돼야 함.
        sem, kw = _make_clients(
            semantic_hits=[
                _hit(chunk_y, "Y", 0.9, "semantic"),
                _hit(chunk_x, "X", 0.8, "semantic"),
            ],
            keyword_hits=[
                _hit(chunk_x, "X", 5.0, "keyword"),
                _hit(chunk_z, "Z", 4.0, "keyword"),
            ],
        )
        client = HybridSearchClient(sem, kw, rrf_k=60)
        hits = await client.search("q", uuid4())
        ids = [h.chunk_id for h in hits]
        # X는 dedupe돼 한 번만, 점수 합산으로 1위.
        assert ids.count(chunk_x) == 1
        assert hits[0].chunk_id == chunk_x

    async def test_unique_hit_keeps_single_backend_score(self):
        chunk_y = uuid4()
        chunk_z = uuid4()
        sem, kw = _make_clients(
            semantic_hits=[_hit(chunk_y, "Y", 0.9, "semantic")],
            keyword_hits=[_hit(chunk_z, "Z", 5.0, "keyword")],
        )
        client = HybridSearchClient(sem, kw, rrf_k=60)
        hits = await client.search("q", uuid4())
        # 둘 다 한쪽에서만 잡힘 — 1위 점수 = 1/(60+0+1) = 1/61.
        assert all(abs(h.score - 1 / 61) < 1e-9 for h in hits)

    async def test_top_k_truncates_final_result(self):
        # 6개 unique chunks, top_k=3 → 결과 3개.
        sem_hits = [_hit(uuid4(), f"S{i}", 0.0, "semantic") for i in range(6)]
        sem, kw = _make_clients(semantic_hits=sem_hits, keyword_hits=[])
        client = HybridSearchClient(sem, kw)
        hits = await client.search("q", uuid4(), top_k=3)
        assert len(hits) == 3

    async def test_fan_out_internal_top_k_larger(self):
        # 외부 top_k=10이면 각 백엔드엔 fan-out으로 15가 요청돼야 함.
        sem, kw = _make_clients(semantic_hits=[], keyword_hits=[])
        client = HybridSearchClient(sem, kw)
        await client.search("q", uuid4(), top_k=10)
        assert sem.search.call_args.args[3] == 15
        assert kw.search.call_args.args[3] == 15

    async def test_score_source_labeled_hybrid(self):
        sem, kw = _make_clients(
            semantic_hits=[_hit(uuid4(), "S", 0.9, "semantic")],
            keyword_hits=[_hit(uuid4(), "K", 5.0, "keyword")],
        )
        client = HybridSearchClient(sem, kw)
        hits = await client.search("q", uuid4())
        assert {h.score_source for h in hits} == {"hybrid"}

    async def test_score_descending(self):
        sem, kw = _make_clients(
            semantic_hits=[_hit(uuid4(), f"S{i}", 0.0, "semantic") for i in range(5)],
            keyword_hits=[],
        )
        client = HybridSearchClient(sem, kw)
        hits = await client.search("q", uuid4())
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)


class TestHybridSearchNegative:
    async def test_both_empty_returns_empty(self):
        sem, kw = _make_clients(semantic_hits=[], keyword_hits=[])
        client = HybridSearchClient(sem, kw)
        assert await client.search("q", uuid4()) == []

    async def test_empty_query_short_circuits(self):
        sem, kw = _make_clients(semantic_hits=[], keyword_hits=[])
        client = HybridSearchClient(sem, kw)
        assert await client.search("", uuid4()) == []
        sem.search.assert_not_awaited()
        kw.search.assert_not_awaited()

    async def test_semantic_failure_propagates_no_silent_fallback(self):
        sem, kw = _make_clients(
            semantic_hits=RuntimeError("sem boom"),
            keyword_hits=[_hit(uuid4(), "K", 5.0, "keyword")],
        )
        client = HybridSearchClient(sem, kw)
        # silent fallback 안 함 — 시맨틱 실패는 전체 실패로 노출.
        with pytest.raises(RuntimeError, match="sem boom"):
            await client.search("q", uuid4())

    async def test_zero_rrf_k_rejected(self):
        sem, kw = _make_clients(semantic_hits=[], keyword_hits=[])
        with pytest.raises(ValueError, match="rrf_k must be positive"):
            HybridSearchClient(sem, kw, rrf_k=0)
