from __future__ import annotations

from dataclasses import dataclass

from src.clients.embedding_client import BgeM3Client
from src.clients.parser import ParserRegistry
from src.clients.reranker_client import BgeRerankerV2M3Client, RerankerClient
from src.core.config import settings
from src.db.session import async_session_maker
from src.services.indexing import ChunkingService, VectorIndexingService
from src.services.retrieval import HybridSearchClient
from src.services.retrieval.factory import create_hybrid_search_client


@dataclass(frozen=True)
class RagWorkflowDependencies:
    indexing_service: VectorIndexingService
    search_client: HybridSearchClient
    reranker: RerankerClient | None


def build_rag_dependencies() -> RagWorkflowDependencies:
    embedding_client = BgeM3Client()

    chunking_service = ChunkingService(
        embedding_client=embedding_client,
    )

    indexing_service = VectorIndexingService(
        parser_registry=ParserRegistry(),
        chunking_service=chunking_service,
        embedding_client=embedding_client,
        session_maker=async_session_maker,
    )

    search_client = create_hybrid_search_client(
        session_maker=async_session_maker,
        embedding_client=embedding_client,
    )

    reranker = BgeRerankerV2M3Client() if settings.reranker_enabled else None

    return RagWorkflowDependencies(
        indexing_service=indexing_service,
        search_client=search_client,
        reranker=reranker,
    )
