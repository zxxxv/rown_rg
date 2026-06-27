from __future__ import annotations

from collections import OrderedDict

import structlog

from src.services.indexing import SourceInput
from src.services.retrieval import SearchHit, rerank_hits
from src.workflows.nodes.dependencies import RagWorkflowDependencies
from src.workflows.nodes.state import (
    IndexedSourceSummary,
    RagGraphState,
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
