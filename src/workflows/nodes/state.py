from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, Field

from src.services.retrieval import SearchHit, Track

SourceType = Literal["library", "upload"]
SourceReliability = Literal["high", "medium", "low"]

ReportPhase = Literal[
    "created",
    "researching",
    "source_review",
    "indexing",
    "retrieving",
    "reranking",
    "writing",
    "qa",
    "final_review",
    "exporting",
    "completed",
    "halted",
    "failed",
]

SourceReviewDecision = Literal[
    "accept_selected",
    "upload_more",
    "search_more",
    "reject_all",
    "halt",
]

FinalReviewDecision = Literal[
    "approve",
    "request_changes",
    "halt",
]


class RagSourceSpec(BaseModel):
    source_type: SourceType
    file_path: Path

    library_node_id: UUID | None = None
    upload_path: str | None = None

    track: Track = "content"
    title: str | None = None
    url: str | None = None
    reliability: SourceReliability | None = None


class IndexedSourceSummary(BaseModel):
    source_id: UUID
    chunks_created: int
    parse_cached: bool
    elapsed_ms: float


class WebSearchQuery(BaseModel):
    query: str
    purpose: str
    track: Literal["policy", "statistics", "research", "media", "general"] = "general"


class WebSearchResult(BaseModel):
    query: str
    title: str
    url: str
    snippet: str | None = None
    source: str | None = None
    published_at: str | None = None


class FetchedWebDocument(BaseModel):
    url: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCandidate(BaseModel):
    id: str
    title: str
    source_type: Literal["web_search", "library", "upload"]
    summary: str
    reliability: SourceReliability | None = None
    url: str | None = None
    is_included: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SectionPlanItem(BaseModel):
    section_id: str
    title: str
    level: int = 1
    purpose: str | None = None


class SectionDraft(BaseModel):
    section_id: str
    title: str
    content: str
    cited_source_ids: list[str] = Field(default_factory=list)


class RagGraphState(TypedDict, total=False):
    project_id: UUID

    # 입력
    query: str
    queries: list[str]
    sources: list[RagSourceSpec]
    track: Track
    top_k: int
    rerank_top_k: int
    use_reranker: bool

    # 보고서 설정
    topic: str
    config: dict[str, Any]

    # 검색 단계
    web_queries: list[WebSearchQuery]
    web_results: list[WebSearchResult]
    fetched_docs: list[FetchedWebDocument]
    source_candidates: list[SourceCandidate]

    # 사용자 게이트
    pending_gate: Literal["source_pool", "final"] | None
    source_review_decision: SourceReviewDecision | None
    selected_candidate_ids: list[str]
    accepted_candidate_ids: list[str]
    uploaded_candidate_ids: list[str]

    # RAG 중간 결과
    indexed_sources: list[IndexedSourceSummary]
    raw_hits: list[SearchHit]
    reranked_hits: list[SearchHit]

    # 작성 단계
    section_plan: list[SectionPlanItem]
    drafts: list[SectionDraft]
    qa_passed: bool
    final_review_decision: FinalReviewDecision | None

    # 최종 결과
    context_pack: str
    export_paths: dict[str, str]

    # 상태
    status: ReportPhase
    errors: list[str]
    progress_events: list[str]


def get_queries(state: RagGraphState) -> list[str]:
    if state.get("queries"):
        return [q for q in state["queries"] if q.strip()]

    query = state.get("query", "")
    return [query] if query.strip() else []
