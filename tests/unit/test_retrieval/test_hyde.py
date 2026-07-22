from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.clients.llm.base import CompletionResponse
from src.services.retrieval._hyde import HyDEQueryGenerator, HyDESearchClient
from src.services.retrieval.base import SearchHit

pytestmark = pytest.mark.asyncio


def _hit(
    *,
    chunk_id: UUID | None = None,
    content: str = "검색 결과",
    score: float = 0.8,
    score_source: str = "semantic",
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


class TestHyDEQueryGenerator:
    async def test_generate_uses_gemini_default_model(self):
        llm_client = MagicMock()
        llm_client.complete = AsyncMock(
            return_value=CompletionResponse(
                content="  가상의 검색용 답변.  ",
                input_tokens=10,
                output_tokens=20,
                cached_input_tokens=0,
                model="gemini-2.5-flash-lite",
                stop_reason="STOP",
            )
        )

        generator = HyDEQueryGenerator(llm_client)

        result = await generator.generate("RAG에서 HyDE는 무엇인가?")

        assert result == "가상의 검색용 답변."

        llm_client.complete.assert_awaited_once()
        request = llm_client.complete.call_args.args[0]

        assert request.model == "gemini-2.5-flash-lite"
        assert request.max_tokens == 256
        assert request.temperature == 0.2
        assert request.system is not None
        assert "HyDE" in request.system
        assert request.messages[0].role == "user"
        assert "RAG에서 HyDE는 무엇인가?" in request.messages[0].content

    async def test_generate_caches_by_normalized_query(self):
        llm_client = MagicMock()
        llm_client.complete = AsyncMock(
            return_value=CompletionResponse(
                content="캐시된 가상 답변",
                input_tokens=10,
                output_tokens=20,
                cached_input_tokens=0,
                model="gemini-2.5-flash-lite",
                stop_reason="STOP",
            )
        )

        generator = HyDEQueryGenerator(llm_client)

        first = await generator.generate("  RAG에서 HyDE는 무엇인가?  ")
        second = await generator.generate("RAG에서 HyDE는 무엇인가?")

        assert first == "캐시된 가상 답변"
        assert second == "캐시된 가상 답변"
        llm_client.complete.assert_awaited_once()

    async def test_empty_query_returns_empty_without_llm_call(self):
        llm_client = MagicMock()
        llm_client.complete = AsyncMock()

        generator = HyDEQueryGenerator(llm_client)

        result = await generator.generate("   ")

        assert result == ""
        llm_client.complete.assert_not_awaited()

    async def test_custom_model_can_be_used(self):
        llm_client = MagicMock()
        llm_client.complete = AsyncMock(
            return_value=CompletionResponse(
                content="가상 답변",
                input_tokens=1,
                output_tokens=1,
                cached_input_tokens=0,
                model="gemini-2.5-flash",
                stop_reason="STOP",
            )
        )

        generator = HyDEQueryGenerator(llm_client, model="gemini-2.5-flash")

        await generator.generate("질문")

        request = llm_client.complete.call_args.args[0]
        assert request.model == "gemini-2.5-flash"


class TestHyDESearchClient:
    async def test_search_uses_hyde_query_for_base_search(self):
        project_id = uuid4()

        base_client = MagicMock()
        base_client.search = AsyncMock(
            return_value=[
                _hit(
                    content="청크",
                    metadata={"page": 3},
                    score_source="semantic",
                )
            ]
        )

        hyde_generator = MagicMock()
        hyde_generator.generate = AsyncMock(return_value="검색용 가상 답변")

        client = HyDESearchClient(base_client, hyde_generator)

        hits = await client.search(
            "원본 질문",
            project_id,
            track="content",
            top_k=5,
        )

        hyde_generator.generate.assert_awaited_once_with("원본 질문")
        base_client.search.assert_awaited_once_with(
            "검색용 가상 답변",
            project_id,
            "content",
            5,
        )

        assert len(hits) == 1
        assert hits[0].metadata["page"] == 3
        assert hits[0].metadata["hyde"] == {
            "used": True,
            "original_query": "원본 질문",
        }
        assert hits[0].score_source == "semantic"

    async def test_hyde_failure_falls_back_to_original_query(self):
        project_id = uuid4()

        base_client = MagicMock()
        base_client.search = AsyncMock(return_value=[_hit(content="fallback 결과")])

        hyde_generator = MagicMock()
        hyde_generator.generate = AsyncMock(side_effect=RuntimeError("LLM failed"))

        client = HyDESearchClient(base_client, hyde_generator)

        hits = await client.search(
            "원본 질문",
            project_id,
            track="content",
            top_k=10,
        )

        hyde_generator.generate.assert_awaited_once_with("원본 질문")
        base_client.search.assert_awaited_once_with(
            "원본 질문",
            project_id,
            "content",
            10,
        )

        assert hits[0].metadata["hyde"] == {
            "used": False,
            "original_query": "원본 질문",
        }

    async def test_empty_query_returns_empty_without_calls(self):
        base_client = MagicMock()
        base_client.search = AsyncMock()

        hyde_generator = MagicMock()
        hyde_generator.generate = AsyncMock()

        client = HyDESearchClient(base_client, hyde_generator)

        result = await client.search("   ", uuid4())

        assert result == []
        hyde_generator.generate.assert_not_awaited()
        base_client.search.assert_not_awaited()

    async def test_metadata_is_added_to_every_hit(self):
        project_id = uuid4()

        base_client = MagicMock()
        base_client.search = AsyncMock(
            return_value=[
                _hit(content="청크1", metadata={"rank": 1}),
                _hit(content="청크2", metadata={"rank": 2}),
            ]
        )

        hyde_generator = MagicMock()
        hyde_generator.generate = AsyncMock(return_value="가상 답변")

        client = HyDESearchClient(base_client, hyde_generator)

        hits = await client.search("질문", project_id)

        assert len(hits) == 2
        assert all(hit.metadata["hyde"]["used"] for hit in hits)
        assert [hit.metadata["rank"] for hit in hits] == [1, 2]
