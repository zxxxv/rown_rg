"""HyDE 쿼리 확장 검증 — 확장·폴백·토큰 귀속 (실LLM·실DB 없음)."""

from __future__ import annotations

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.clients.llm.token_tracker import get_operation
from src.services.retrieval._hyde import make_hyde_expander


class _StubClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.requests: list[CompletionRequest] = []
        self.seen_operations: list[str | None] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        self.seen_operations.append(get_operation())
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


class _BoomClient:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise RuntimeError("provider down")


class TestHydeExpander:
    async def test_prepends_original_query_to_passage(self):
        client = _StubClient(
            "고령화율은 2023년 기준 18.4%로 상승했다. 통계청 장래인구추계에 따르면..."
        )
        expand = make_hyde_expander(client=client)
        out = await expand("고령화 추이와 전망")
        assert out.startswith("고령화 추이와 전망\n")
        assert "고령화율" in out

    async def test_uses_configured_model(self):
        client = _StubClient("단락")
        expand = make_hyde_expander(client=client, model="gemini-2.5-flash-lite")
        await expand("주제")
        assert client.requests[0].model == "gemini-2.5-flash-lite"

    async def test_empty_passage_falls_back_to_query(self):
        expand = make_hyde_expander(client=_StubClient("   "))
        assert await expand("고령화 추이") == "고령화 추이"

    async def test_provider_error_falls_back_to_query(self):
        expand = make_hyde_expander(client=_BoomClient())
        assert await expand("고령화 추이") == "고령화 추이"

    async def test_token_context_operation_attributed(self):
        client = _StubClient("단락")
        expand = make_hyde_expander(client=client)
        await expand("주제")
        assert client.seen_operations == ["retrieval.hyde"]


class TestSemanticClientWiring:
    async def test_expander_called_before_embedding(self):
        """SemanticSearchClient가 임베딩 전에 확장기를 호출하는지 — 임베더 stub로 검증."""
        from src.services.retrieval._semantic import SemanticSearchClient

        embedded: list[str] = []

        class _StubEmbedder:
            async def embed(self, text: str):
                embedded.append(text)
                raise _StopAfterEmbed  # DB 단계 진입 전에 중단 (세션 불필요)

        class _StopAfterEmbed(Exception):
            pass

        async def fake_expander(query: str) -> str:
            return f"{query}\n가설 단락"

        client = SemanticSearchClient(
            session_factory=None,  # type: ignore[arg-type]  # embed에서 중단되므로 미사용
            embedder=_StubEmbedder(),  # type: ignore[arg-type]
            query_expander=fake_expander,
        )
        from uuid import uuid4

        with pytest.raises(_StopAfterEmbed):
            await client.search("고령화 추이", uuid4())
        assert embedded == ["고령화 추이\n가설 단락"]
