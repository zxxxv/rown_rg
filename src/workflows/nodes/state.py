from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel

from src.services.retrieval import SearchHit, Track

SourceType = Literal["library", "upload"]


class RageSourceSpec(BaseModel):
    source_type: SourceType
    file_path: Path

    library_node_id: UUID | None = None
    upload_path: str | None = None

    track: Track = "content"
    title: str | None = None
    url: str | None = None
    reliability: Literal["high", "medium", "low"] | None = None


class IndexedSourceSummary(BaseModel):
    source_id: UUID
    chunks_created: int
    parse_cached: bool
    elapsed_ms: float


class RagGraphState(TypedDict, total=False):
    project_id: UUID

    # 입력
    query: str
    queries: list[str]
    sources: list[RageSourceSpec]
    track: Track
    top_k: int
    rerank_top_k: int
    use_reranker: bool

    # 중간 결과
    indexed_sources: list[IndexedSourceSummary]
    raw_hits: list[SearchHit]
    reranked_hits: list[SearchHit]

    # 최종 결과
    context_pack: str
    stauts: Literal[
        "created",
        "indexing",
        "retrieving",
        "reranking",
        "completed",
        "failed",
    ]
    errors: list[str]


def get_queries(state: RagGraphState) -> list[str]:
    if state.get("queries"):
        return [q for q in state["queries"] if q.strip()]

    query = state.get("query", "")
    return [query] if query.strip() else []
