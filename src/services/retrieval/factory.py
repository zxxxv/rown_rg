from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.clients.embedding_client import EmbeddingClient
from src.services.retrieval._keyword import KeywordSearchClient
from src.services.retrieval._semantic import SemanticSearchClient
from src.services.retrieval.hybrid import HybridSearchClient


def create_hybrid_search_client(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    embedding_client: EmbeddingClient,
) -> HybridSearchClient:
    semantic_client = SemanticSearchClient(
        session_factory=session_maker,
        embedder=embedding_client,
    )
    keyword_client = KeywordSearchClient(
        session_factory=session_maker,
    )

    return HybridSearchClient(
        semantic_client=semantic_client,
        keyword_client=keyword_client,
    )
