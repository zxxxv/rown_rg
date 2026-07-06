from __future__ import annotations

from collections import OrderedDict
from uuid import uuid4

import structlog

from src.services.indexing import SourceInput
from src.services.retrieval import SearchHit, rerank_hits
from src.workflows.nodes.dependencies import RagWorkflowDependencies
from src.workflows.nodes.state import (
    FetchedWebDocument,
    IndexedSourceSummary,
    RagGraphState,
    SectionDraft,
    SectionPlanItem,
    SourceCandidate,
    WebSearchQuery,
    WebSearchResult,
    get_queries,
)

logger = structlog.get_logger(__name__)


def make_index_sources_node(deps: RagWorkflowDependencies):
    async def index_sources_node(state: RagGraphState) -> RagGraphState:
        project_id = state["project_id"]
        sources = state.get("sources", [])

        if not sources:
            return {
                **state,
                "status": "indexing",
                "indexed_sources": [],
            }

        indexed: list[IndexedSourceSummary] = []
        errors = list(state.get("errors", []))

        for source in sources:
            try:
                result = await deps.indexing_service.index_source(
                    SourceInput(
                        project_id=project_id,
                        source_type=source.source_type,
                        file_path=source.file_path,
                        library_node_id=source.library_node_id,
                        upload_path=source.upload_path,
                        track=source.track,
                        title=source.title,
                        url=source.url,
                        reliability=source.reliability,
                    )
                )

                indexed.append(
                    IndexedSourceSummary(
                        source_id=result.source_id,
                        chunks_created=result.chunks_created,
                        parse_cached=result.parse_cached,
                        elapsed_ms=result.elapsed_ms,
                    )
                )

            except Exception as exc:
                logger.exception(
                    "rag_graph.index_source.failed",
                    project_id=str(project_id),
                    file_path=str(source.file_path),
                    error_type=type(exc).__name__,
                )
                errors.append(f"indexing failed: {source.file_path} ({type(exc).__name__})")

        return {
            **state,
            "status": "indexing",
            "indexed_sources": indexed,
            "errors": errors,
        }

    return index_sources_node


def make_retrieve_node(deps: RagWorkflowDependencies):
    async def retrieve_node(state: RagGraphState) -> RagGraphState:
        project_id = state["project_id"]
        queries = get_queries(state)

        if not queries:
            return {
                **state,
                "status": "failed",
                "raw_hits": [],
                "errors": [*state.get("errors", []), "query or queries is required"],
            }

        track = state.get("track", "content")
        top_k = state.get("top_k", 20)

        merged: OrderedDict[str, SearchHit] = OrderedDict()

        for query in queries:
            hits = await deps.search_client.search(
                query=query,
                project_id=project_id,
                track=track,
                top_k=top_k,
            )

            for hit in hits:
                key = str(hit.chunk_id)
                if key not in merged or hit.score > merged[key].score:
                    merged[key] = hit

        raw_hits = sorted(
            merged.values(),
            key=lambda hit: hit.score,
            reverse=True,
        )[:top_k]

        return {
            **state,
            "status": "retrieving",
            "raw_hits": raw_hits,
        }

    return retrieve_node


def make_rerank_node(deps: RagWorkflowDependencies):
    async def rerank_node(state: RagGraphState) -> RagGraphState:
        hits = state.get("raw_hits", [])
        queries = get_queries(state)

        if not hits:
            return {
                **state,
                "status": "reranking",
                "reranked_hits": [],
            }

        if not state.get("use_reranker", True) or deps.reranker is None:
            return {
                **state,
                "status": "reranking",
                "reranked_hits": hits[: state.get("rerank_top_k", 10)],
            }

        # 여러 query가 들어온 경우 첫 번째 query를 대표 query로 사용.
        # 섹션별 검색에서는 graph를 query 단위로 여러 번 호출하는 쪽이 더 안전함.
        query = queries[0]
        reranked = await rerank_hits(
            reranker=deps.reranker,
            query=query,
            hits=hits,
            top_k=state.get("rerank_top_k", 10),
        )

        return {
            **state,
            "status": "reranking",
            "reranked_hits": reranked,
        }

    return rerank_node


async def build_context_pack_node(state: RagGraphState) -> RagGraphState:
    hits = state.get("reranked_hits") or state.get("raw_hits", [])

    if not hits:
        return {
            **state,
            "status": "completed",
            "context_pack": "",
        }

    lines: list[str] = []

    for idx, hit in enumerate(hits, start=1):
        header_path = hit.metadata.get("header_path")
        chunk_type = hit.metadata.get("chunk_type")

        meta_parts = [
            f"chunk_id={hit.chunk_id}",
            f"source_id={hit.source_id}",
            f"chunk_index={hit.chunk_index}",
            f"score={hit.score:.4f}",
            f"score_source={hit.score_source}",
        ]

        if header_path:
            meta_parts.append(f"header_path={header_path}")

        if chunk_type:
            meta_parts.append(f"chunk_type={chunk_type}")

        lines.append(f"[{idx}] " + " | ".join(meta_parts))
        lines.append(hit.content.strip())
        lines.append("")

    return {
        **state,
        "status": "completed",
        "context_pack": "\n".join(lines).strip(),
    }


async def generate_report_queries_node(state: RagGraphState) -> RagGraphState:
    topic = state.get("topic") or state.get("query") or "보고서 주제 미정"

    web_queries = [
        WebSearchQuery(
            query=f"{topic} 최신 정책 자료",
            purpose="정부·공공기관 정책 근거 확보",
            track="policy",
        ),
        WebSearchQuery(
            query=f"{topic} 통계 현황",
            purpose="정량 지표와 현황 통계 확보",
            track="statistics",
        ),
        WebSearchQuery(
            query=f"{topic} 선행연구 보고서",
            purpose="연구기관·학술 근거 확보",
            track="research",
        ),
        WebSearchQuery(
            query=f"{topic} 주요 쟁점 보도",
            purpose="최근 이슈와 사회적 쟁점 파악",
            track="media",
        ),
    ]

    return {
        **state,
        "status": "researching",
        "web_queries": web_queries,
        "queries": [q.query for q in web_queries],
        "progress_events": [
            *state.get("progress_events", []),
            f"query_generate: {len(web_queries)} web queries generated",
        ],
    }


async def web_search_stub_node(state: RagGraphState) -> RagGraphState:
    results: list[WebSearchResult] = []

    for idx, query in enumerate(state.get("web_queries", []), start=1):
        results.append(
            WebSearchResult(
                query=query.query,
                title=f"[STUB] {query.track} 검색 결과 {idx}",
                url=f"https://example.com/report-source-{idx}",
                snippet=f"{query.query}에 대한 검색 결과 snippet",
                source="web_search_stub",
            )
        )

    return {
        **state,
        "status": "researching",
        "web_results": results,
        "progress_events": [
            *state.get("progress_events", []),
            f"web_search: {len(results)} results",
        ],
    }


async def web_fetch_stub_node(state: RagGraphState) -> RagGraphState:
    fetched_docs: list[FetchedWebDocument] = []

    for result in state.get("web_results", []):
        fetched_docs.append(
            FetchedWebDocument(
                url=result.url,
                title=result.title,
                content=(
                    f"{result.title} 원문 stub. " "실제 구현에서는 web_fetch 결과 본문이 들어간다."
                ),
                metadata={
                    "query": result.query,
                    "source": result.source,
                    "fetch_status": "stubbed",
                },
            )
        )

    return {
        **state,
        "status": "researching",
        "fetched_docs": fetched_docs,
        "progress_events": [
            *state.get("progress_events", []),
            f"web_fetch: {len(fetched_docs)} documents fetched",
        ],
    }


async def summarize_sources_stub_node(state: RagGraphState) -> RagGraphState:
    candidates: list[SourceCandidate] = []

    for doc in state.get("fetched_docs", []):
        candidates.append(
            SourceCandidate(
                id=f"web_{uuid4().hex[:8]}",
                title=doc.title,
                url=doc.url,
                source_type="web_search",
                summary=(f"{doc.title} 요약 stub. " "실제 구현에서는 Haiku 요약 결과가 들어간다."),
                reliability="medium",
                is_included=None,
                metadata={
                    **doc.metadata,
                    "summary_model": "stub",
                },
            )
        )

    return {
        **state,
        "status": "researching",
        "source_candidates": candidates,
        "progress_events": [
            *state.get("progress_events", []),
            f"summarize_sources: {len(candidates)} candidates",
        ],
    }


async def save_candidates_stub_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "source_review",
        "source_candidates": state.get("source_candidates", []),
        "progress_events": [
            *state.get("progress_events", []),
            "candidate_save: candidates saved without chunking/embedding",
        ],
    }


async def source_review_gate_node(state: RagGraphState) -> RagGraphState:
    decision = state.get("source_review_decision")

    if decision is None:
        return {
            **state,
            "status": "source_review",
            "pending_gate": "source_pool",
            "progress_events": [
                *state.get("progress_events", []),
                "source_review_gate: waiting for user decision",
            ],
        }

    return {
        **state,
        "status": "source_review",
        "pending_gate": None,
        "progress_events": [
            *state.get("progress_events", []),
            f"source_review_gate: decision={decision}",
        ],
    }


def route_after_source_review(state: RagGraphState) -> str:
    decision = state.get("source_review_decision")

    if decision is None:
        return "wait"
    if decision == "accept_selected":
        return "accept_selected"
    if decision == "upload_more":
        return "upload_more"
    if decision in {"search_more", "reject_all"}:
        return "search_more"
    return "halt"


async def wait_for_source_review_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "source_review",
        "pending_gate": "source_pool",
    }


async def upload_source_stub_node(state: RagGraphState) -> RagGraphState:
    uploaded = SourceCandidate(
        id=f"upload_{uuid4().hex[:8]}",
        title="[STUB] 사용자 업로드 자료",
        source_type="upload",
        summary="사용자 업로드 자료 요약 stub",
        reliability="medium",
        is_included=None,
        metadata={"upload_status": "stubbed"},
    )

    return {
        **state,
        "status": "source_review",
        "source_review_decision": None,
        "uploaded_candidate_ids": [
            *state.get("uploaded_candidate_ids", []),
            uploaded.id,
        ],
        "source_candidates": [
            *state.get("source_candidates", []),
            uploaded,
        ],
        "progress_events": [
            *state.get("progress_events", []),
            "upload_source: uploaded source candidate added",
        ],
    }


async def prepare_search_more_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "researching",
        "source_review_decision": None,
        "pending_gate": None,
        "progress_events": [
            *state.get("progress_events", []),
            "search_more: re-enter research loop",
        ],
    }


async def accept_candidates_stub_node(state: RagGraphState) -> RagGraphState:
    selected = state.get("selected_candidate_ids", [])

    if not selected:
        selected = [c.id for c in state.get("source_candidates", [])]

    return {
        **state,
        "status": "indexing",
        "accepted_candidate_ids": selected,
        "progress_events": [
            *state.get("progress_events", []),
            f"accept_candidates: {len(selected)} candidates accepted",
        ],
    }


async def plan_sections_stub_node(state: RagGraphState) -> RagGraphState:
    topic = state.get("topic") or state.get("query") or "보고서 주제"

    sections = [
        SectionPlanItem(
            section_id="1",
            title="문제 정의 및 배경",
            level=1,
            purpose=f"{topic}의 배경과 필요성 정리",
        ),
        SectionPlanItem(
            section_id="2",
            title="자료 기반 현황 분석",
            level=1,
            purpose="수집 자료 기반 핵심 현황 분석",
        ),
        SectionPlanItem(
            section_id="3",
            title="대안 및 실행 전략",
            level=1,
            purpose="실행 방안과 기대효과 제시",
        ),
    ]

    return {
        **state,
        "status": "writing",
        "section_plan": sections,
        "progress_events": [
            *state.get("progress_events", []),
            f"plan_sections: {len(sections)} sections",
        ],
    }


async def write_sections_stub_node(state: RagGraphState) -> RagGraphState:
    accepted_ids = state.get("accepted_candidate_ids", [])
    drafts: list[SectionDraft] = []

    for section in state.get("section_plan", []):
        drafts.append(
            SectionDraft(
                section_id=section.section_id,
                title=section.title,
                content=(
                    f"{section.title} 본문 stub. "
                    "실제 구현에서는 context_pack과 근거 청크 기반 초안이 들어간다."
                ),
                cited_source_ids=accepted_ids,
            )
        )

    return {
        **state,
        "status": "writing",
        "drafts": drafts,
        "progress_events": [
            *state.get("progress_events", []),
            f"write_sections: {len(drafts)} drafts",
        ],
    }


async def qa_stub_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "qa",
        "qa_passed": True,
        "progress_events": [
            *state.get("progress_events", []),
            "qa: passed by stub",
        ],
    }


def route_after_qa(state: RagGraphState) -> str:
    return "passed" if state.get("qa_passed", False) else "failed"


async def final_review_gate_node(state: RagGraphState) -> RagGraphState:
    decision = state.get("final_review_decision")

    if decision is None:
        return {
            **state,
            "status": "final_review",
            "pending_gate": "final",
            "progress_events": [
                *state.get("progress_events", []),
                "final_review_gate: waiting for user decision",
            ],
        }

    return {
        **state,
        "status": "final_review",
        "pending_gate": None,
        "progress_events": [
            *state.get("progress_events", []),
            f"final_review_gate: decision={decision}",
        ],
    }


def route_after_final_review(state: RagGraphState) -> str:
    decision = state.get("final_review_decision")

    if decision is None:
        return "wait"
    if decision == "approve":
        return "approve"
    if decision == "request_changes":
        return "request_changes"
    return "halt"


async def wait_for_final_review_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "final_review",
        "pending_gate": "final",
    }


async def prepare_revision_stub_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "writing",
        "final_review_decision": None,
        "progress_events": [
            *state.get("progress_events", []),
            "revision: re-enter writing loop",
        ],
    }


async def export_report_stub_node(state: RagGraphState) -> RagGraphState:
    project_id = state.get("project_id")

    return {
        **state,
        "status": "exporting",
        "export_paths": {
            "markdown": f"exports/{project_id}/report.md",
            "hwpx": f"exports/{project_id}/report.hwpx",
        },
        "progress_events": [
            *state.get("progress_events", []),
            "export: markdown/hwpx paths generated by stub",
        ],
    }


async def complete_report_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "completed",
        "pending_gate": None,
        "progress_events": [
            *state.get("progress_events", []),
            "complete: report workflow completed",
        ],
    }


async def halt_report_node(state: RagGraphState) -> RagGraphState:
    return {
        **state,
        "status": "halted",
        "pending_gate": None,
        "progress_events": [
            *state.get("progress_events", []),
            "halt: report workflow halted",
        ],
    }
