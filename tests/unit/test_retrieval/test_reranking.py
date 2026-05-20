"""rerank_hits 헬퍼 테스트 — adapter는 mock으로 차단하고 헬퍼 합성 로직만 검증."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.services.retrieval import rerank_hits
from src.services.retrieval.base import SearchHit

pytestmark = pytest.mark.asyncio


def _hit(
    *,
    chunk_id: UUID | None = None,
    content: str = "내용",
    score: float = 0.5,
    score_source: str = "hybrid",
    metadata: dict | None = None,
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id or uuid4(),
        content=content,
        source_id=uuid4(),
        chunk_index=0,
        metadata=metadata or {},
        score=score,
        score_source=score_source,
    )


class TestRerankHits:
    async def test_empty_hits_returns_empty(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = []
        result = await rerank_hits(mock_reranker, "쿼리", [], top_k=10)
        assert result == []
        # 빈 입력은 어댑터를 호출하지 않아야 함 — 헬퍼 단에서 short-circuit
        mock_reranker.score_pairs.assert_not_called()

    async def test_sorts_descending_by_reranker_score(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.5, 0.9, 0.3]

        hits = [
            _hit(content="청크1", score=0.7),
            _hit(content="청크2", score=0.6),
            _hit(content="청크3", score=0.8),
        ]
        result = await rerank_hits(mock_reranker, "쿼리", hits, top_k=3)

        assert [h.score for h in result] == [0.9, 0.5, 0.3]
        assert [h.content for h in result] == ["청크2", "청크1", "청크3"]

    async def test_truncates_to_top_k(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.1, 0.5, 0.9, 0.3, 0.7]

        hits = [_hit(content=f"청크{i}") for i in range(5)]
        result = await rerank_hits(mock_reranker, "쿼리", hits, top_k=2)

        assert len(result) == 2
        assert [h.score for h in result] == [0.9, 0.7]

    async def test_score_source_set_to_reranker(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.5, 0.9]

        hits = [
            _hit(content="청크1", score=0.7, score_source="hybrid"),
            _hit(content="청크2", score=0.6, score_source="semantic"),
        ]
        result = await rerank_hits(mock_reranker, "쿼리", hits, top_k=2)

        assert all(h.score_source == "reranker" for h in result)

    async def test_preserves_original_score_in_metadata(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.5, 0.9, 0.3]

        hits = [
            _hit(content="청크1", score=0.7, score_source="hybrid"),
            _hit(content="청크2", score=0.6, score_source="semantic"),
            _hit(content="청크3", score=0.8, score_source="keyword"),
        ]
        result = await rerank_hits(mock_reranker, "쿼리", hits, top_k=3)

        # 정렬 후 top: 청크2(0.9) > 청크1(0.5) > 청크3(0.3)
        assert result[0].metadata["original_score"] == 0.6
        assert result[0].metadata["original_score_source"] == "semantic"
        assert result[1].metadata["original_score"] == 0.7
        assert result[1].metadata["original_score_source"] == "hybrid"
        assert result[2].metadata["original_score"] == 0.8
        assert result[2].metadata["original_score_source"] == "keyword"

    async def test_existing_metadata_keys_preserved(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.9]

        hit = _hit(content="청크1", metadata={"page": 3, "section": "intro"})
        result = await rerank_hits(mock_reranker, "쿼리", [hit], top_k=1)

        # 기존 metadata는 그대로 유지, original_score 키만 추가
        assert result[0].metadata["page"] == 3
        assert result[0].metadata["section"] == "intro"
        assert "original_score" in result[0].metadata

    async def test_reranker_receives_passages_in_hit_order(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.5, 0.5, 0.5]

        hits = [
            _hit(content="첫째"),
            _hit(content="둘째"),
            _hit(content="셋째"),
        ]
        await rerank_hits(mock_reranker, "쿼리", hits, top_k=3)

        # 어댑터에 전달된 passages가 hits 순서와 일치하는지
        call_args = mock_reranker.score_pairs.call_args
        assert call_args.args[0] == "쿼리"
        assert call_args.args[1] == ["첫째", "둘째", "셋째"]

    async def test_chunk_id_preserved_through_rerank(self):
        mock_reranker = AsyncMock()
        mock_reranker.score_pairs.return_value = [0.5, 0.9]

        id_a = uuid4()
        id_b = uuid4()
        hits = [
            _hit(chunk_id=id_a, content="a"),
            _hit(chunk_id=id_b, content="b"),
        ]
        result = await rerank_hits(mock_reranker, "쿼리", hits, top_k=2)

        # b가 더 높은 점수 → 첫 자리. chunk_id는 hit별 정체성이라 정렬 후도 보존돼야 함.
        assert result[0].chunk_id == id_b
        assert result[1].chunk_id == id_a
