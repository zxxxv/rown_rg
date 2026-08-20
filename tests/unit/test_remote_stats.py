"""원격 호출 통계 — "조용한 폴백"이 세어지는지.

핵심은 임베딩 쪽 의미다: ``fallback_items_total``이 dtype이 다른 채 색인에
들어갔을 수 있는 벡터 수라, 이 카운터가 새면 재색인 신호를 놓친다.
"""

from __future__ import annotations

import time

import httpx

from src.clients.remote_stats import RemoteCallStats


class TestCounters:
    def test_fallback_accumulates_by_reason_and_items(self):
        s = RemoteCallStats()
        s.record_fallback("error", items=96)
        s.record_fallback("cooldown", items=256)
        s.record_fallback("cooldown", items=256)
        assert s.fallback_total == {"error": 1, "cooldown": 2}
        assert s.fallback_items_total == 96 + 256 + 256
        assert s.last_fallback_at is not None

    def test_snapshot_reports_cooldown_remaining(self):
        s = RemoteCallStats()
        snap = s.snapshot(disabled_until_monotonic=time.monotonic() + 30.0)
        assert snap["in_cooldown"] is True
        assert 0 < snap["cooldown_remaining_s"] <= 30.0

    def test_snapshot_without_cooldown(self):
        s = RemoteCallStats()
        s.record_ok()
        snap = s.snapshot(disabled_until_monotonic=0.0)
        assert snap["in_cooldown"] is False
        assert snap["cooldown_remaining_s"] == 0.0
        assert snap["remote_ok_total"] == 1


class TestClientIntegration:
    """실제 클라이언트가 성공/폴백 경로에서 카운터를 올리는지 — 대역 전송으로 확인."""

    def test_embedding_fallback_counts_texts(self):
        import asyncio

        from src.clients.embedding_client import EmbeddingResult
        from src.clients.onnx_text_embedder import DIMENSION
        from src.clients.remote_embedding_client import RemoteEmbeddingClient

        class _FakeLocal:
            async def embed_batch(self, texts):
                return [
                    EmbeddingResult(embedding=[0.5] * DIMENSION, text=t, cached=False)
                    for t in texts
                ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "boom"})

        client = RemoteEmbeddingClient(
            base_url="http://gpu.local:8009",
            token="secret",
            timeout_s=5.0,
            connect_timeout_s=1.0,
            cooldown_s=60.0,
            chunk=256,
            fallback="local",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            local_factory=lambda: _FakeLocal(),
        )
        asyncio.run(client.embed_batch(["a", "b", "c"]))

        snap = client.stats_snapshot()
        assert snap["fallback_total"] == {"error": 1, "cooldown": 0}
        assert snap["fallback_items_total"] == 3
        assert snap["in_cooldown"] is True
        assert snap["last_error"] is not None

    def test_reranker_success_counts_ok(self):
        import asyncio

        from src.clients.remote_reranker_client import RemoteRerankerClient

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"scores": [0.9, 0.1]})

        client = RemoteRerankerClient(
            base_url="http://gpu.local:8009",
            token="secret",
            timeout_s=5.0,
            connect_timeout_s=1.0,
            cooldown_s=60.0,
            fallback="local",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        asyncio.run(client.score_pairs("q", ["p1", "p2"]))

        snap = client.stats_snapshot()
        assert snap["remote_ok_total"] == 1
        assert snap["fallback_items_total"] == 0
        assert snap["in_cooldown"] is False
