"""HyDE(Hypothetical Document Embeddings) 쿼리 확장 — dense 검색 전용.

짧은 섹션 제목 쿼리로는 임베딩 표현이 빈약하다. LLM이 그 주제로 '실제 보고서에
있을 법한 가설 단락'을 쓰게 하고, 원 쿼리와 이어붙여 임베딩해 dense 검색의
표현력을 높인다(Gao et al., 2022). keyword(BM25) 검색에는 적용하지 않는다 —
정확 용어 매칭은 원 쿼리가 유리하므로 SemanticSearchClient의 query_expander로만
주입한다.

- 기본 off(settings.hyde_enabled). 켠 뒤 효과는 eval_search(nDCG)로 측정해 결정.
- 생성 텍스트는 임베딩에만 쓰이고 사용자에게 노출되지 않는다 — 수치 정확성 불요.
- 실패는 원 쿼리 폴백: 선택 기능이 검색 자체를 막아선 안 된다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context

logger = structlog.get_logger(__name__)

QueryExpander = Callable[[str], Awaitable[str]]

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_MAX_TOKENS = 512  # BGE-M3 입력 상한(512토큰)에 맞춰 짧게

_SYSTEM = (
    "너는 검색 보조 작성기다. 입력된 주제에 대해, 정부·공공 보고서 본문에 실제로 "
    "있을 법한 단락 하나를 한국어로 작성하라. 3~5문장으로 짧게, 관련 전문용어·기관명·"
    "지표명을 포함하라. 이 글은 검색용 임베딩에만 쓰이므로 수치가 정확할 필요는 없다. "
    "설명 없이 단락 텍스트만 출력하라."
)


def make_hyde_expander(
    *,
    model: str = DEFAULT_MODEL,
    client: LLMClient | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> QueryExpander:
    """쿼리 → '원 쿼리 + 가설 단락' 확장 함수를 만든다.

    원 쿼리를 앞에 붙이는 이유: 가설 단락이 주제를 벗어나도(할루시네이션) 임베딩이
    원 쿼리 방향을 유지하게 하는 안전장치다.
    """

    async def expand(query: str) -> str:
        try:
            with token_context(user_id=user_id, project_id=project_id, operation="retrieval.hyde"):
                response = await (client or get_llm_client()).complete(
                    CompletionRequest(
                        model=model,
                        system=_SYSTEM,
                        messages=[Message(role="user", content=query)],
                        max_tokens=max_tokens,
                        cache_key=None,
                    )
                )
            passage = response.content.strip()
            if not passage:
                logger.warning("hyde.empty_passage", query=query, model=model)
                return query
            return f"{query}\n{passage}"
        except Exception:
            # 확장 실패가 검색을 막으면 안 된다 — 원 쿼리로 폴백하고 경고만 남긴다.
            logger.warning("hyde.expand_failed", query=query, model=model, exc_info=True)
            return query

    return expand
