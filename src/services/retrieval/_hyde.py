from __future__ import annotations

from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.models import default_id
from src.services.retrieval.base import SearchClient, SearchHit, Track

logger = structlog.get_logger(__name__)


_HYDE_SYSTEM_PROMPT = """RAG 검색 품질 향상을 위한 HyDE 생성기.
사용자 질문에 직접 답하는 것처럼 보이는 짧은 가상 문서를 작성.
이 문서는 사용자에게 보여주지 않고 벡터 검색용 임베딩 입력으로만 사용됨.
마크다운, 목록, 출처 표기는 쓰지 말고 3~5문장으로 작성.
"""


def _build_hyde_prompt(query: str) -> str:
    return f"""사용자 질문:
{query}

위 질문에 답하는 것처럼 보이는 검색용 가상 답변을 작성.
검색에 도움이 되는 핵심 용어, 동의어, 관련 개념을 포함.
"""


class HyDEQueryGenerator:
    """LLM을 통해 hypothetical answer를 생성."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        model: str | None = None,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> None:
        self._llm_client = llm_client
        self._model = model or default_id("gemini")
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def generate(self, query: str) -> str:
        query = query.strip()

        if not query:
            return ""

        request = CompletionRequest(
            model=self._model,
            system=_HYDE_SYSTEM_PROMPT,
            messages=[
                Message(
                    role="user",
                    content=_build_hyde_prompt(query),
                )
            ],
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

        response = await self._llm_client.complete(request)

        return response.content.strip()


class HyDESearchClient(SearchClient):
    """HyDE query로 기존 검색기를 감싸는 SearchClient wrapper."""

    def __init__(
        self,
        base_client: SearchClient,
        hyde_generator: HyDEQueryGenerator,
    ) -> None:
        self._base_client = base_client
        self._hyde_generator = hyde_generator

    async def search(
        self,
        query: str,
        project_id: UUID,
        track: Track = "content",
        top_k: int = 50,
    ) -> list[SearchHit]:
        original_query = query.strip()

        if not original_query:
            return []

        try:
            hyde_query = await self._hyde_generator.generate(original_query)
        except Exception:
            logger.warning(
                "hyde.generate_failed",
                project_id=str(project_id),
                track=track,
                query_length=len(original_query),
                exc_info=True,
            )
            hyde_query = ""

        search_query = hyde_query or original_query

        hits = await self._base_client.search(
            search_query,
            project_id,
            track,
            top_k,
        )

        return [
            self._with_hyde_metadata(
                hit,
                original_query=original_query,
                used_hyde=bool(hyde_query),
            )
            for hit in hits
        ]

    def _with_hyde_metadata(
        self,
        hit: SearchHit,
        *,
        original_query: str,
        used_hyde: bool,
    ) -> SearchHit:
        metadata = dict(hit.metadata)
        metadata["hyde"] = {
            "used": used_hyde,
            "original_query": original_query,
        }

        return hit.model_copy(
            update={
                "metadata": metadata,
            }
        )
