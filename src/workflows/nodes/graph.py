from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.workflows.nodes.dependencies import (
    RagWorkflowDependencies,
    build_rag_dependencies,
)
from src.workflows.nodes.nodes import (
    accept_candidates_stub_node,
    build_context_pack_node,
    complete_report_node,
    export_report_stub_node,
    final_review_gate_node,
    generate_report_queries_node,
    halt_report_node,
    make_index_sources_node,
    make_rerank_node,
    make_retrieve_node,
    plan_sections_stub_node,
    prepare_revision_stub_node,
    prepare_search_more_node,
    qa_stub_node,
    route_after_final_review,
    route_after_qa,
    route_after_source_review,
    save_candidates_stub_node,
    source_review_gate_node,
    summarize_sources_stub_node,
    upload_source_stub_node,
    wait_for_final_review_node,
    wait_for_source_review_node,
    web_fetch_stub_node,
    web_search_stub_node,
    write_sections_stub_node,
)
from src.workflows.nodes.state import RagGraphState


def should_index(state: RagGraphState) -> str:
    if state.get("sources"):
        return "index_sources"
    return "retrieve"


def should_rerank(state: RagGraphState) -> str:
    if state.get("raw_hits"):
        return "rerank"
    return "build_context_pack"


def build_rag_graph(
    deps: RagWorkflowDependencies | None = None,
):
    deps = deps or build_rag_dependencies()

    graph = StateGraph(RagGraphState)

    graph.add_node("index_sources", make_index_sources_node(deps))
    graph.add_node("retrieve", make_retrieve_node(deps))
    graph.add_node("rerank", make_rerank_node(deps))
    graph.add_node("build_context_pack", build_context_pack_node)

    graph.add_conditional_edges(
        START,
        should_index,
        {
            "index_sources": "index_sources",
            "retrieve": "retrieve",
        },
    )

    graph.add_edge("index_sources", "retrieve")

    graph.add_conditional_edges(
        "retrieve",
        should_rerank,
        {
            "rerank": "rerank",
            "build_context_pack": "build_context_pack",
        },
    )

    graph.add_edge("rerank", "build_context_pack")
    graph.add_edge("build_context_pack", END)

    return graph.compile()


def build_report_graph(
    deps: RagWorkflowDependencies | None = None,
):
    deps = deps or build_rag_dependencies()

    graph = StateGraph(RagGraphState)

    # 검색 단계
    graph.add_node("generate_report_queries", generate_report_queries_node)
    graph.add_node("web_search", web_search_stub_node)
    graph.add_node("web_fetch", web_fetch_stub_node)
    graph.add_node("summarize_sources", summarize_sources_stub_node)
    graph.add_node("save_candidates", save_candidates_stub_node)

    # 자료 검토 게이트
    graph.add_node("source_review_gate", source_review_gate_node)
    graph.add_node("wait_for_source_review", wait_for_source_review_node)
    graph.add_node("upload_source", upload_source_stub_node)
    graph.add_node("prepare_search_more", prepare_search_more_node)
    graph.add_node("accept_candidates", accept_candidates_stub_node)
    graph.add_node("halt_report", halt_report_node)

    # 기존 RAG 인덱싱/검색 노드 재사용
    graph.add_node("index_sources", make_index_sources_node(deps))
    graph.add_node("retrieve", make_retrieve_node(deps))
    graph.add_node("rerank", make_rerank_node(deps))
    graph.add_node("build_context_pack", build_context_pack_node)

    # 작성/QA/최종 검토/export
    graph.add_node("plan_sections", plan_sections_stub_node)
    graph.add_node("write_sections", write_sections_stub_node)
    graph.add_node("qa", qa_stub_node)
    graph.add_node("final_review_gate", final_review_gate_node)
    graph.add_node("wait_for_final_review", wait_for_final_review_node)
    graph.add_node("prepare_revision", prepare_revision_stub_node)
    graph.add_node("export_report", export_report_stub_node)
    graph.add_node("complete_report", complete_report_node)

    # Research
    graph.add_edge(START, "generate_report_queries")
    graph.add_edge("generate_report_queries", "web_search")
    graph.add_edge("web_search", "web_fetch")
    graph.add_edge("web_fetch", "summarize_sources")
    graph.add_edge("summarize_sources", "save_candidates")
    graph.add_edge("save_candidates", "source_review_gate")

    # Source review branch
    graph.add_conditional_edges(
        "source_review_gate",
        route_after_source_review,
        {
            "accept_selected": "accept_candidates",
            "upload_more": "upload_source",
            "search_more": "prepare_search_more",
            "wait": "wait_for_source_review",
            "halt": "halt_report",
        },
    )

    graph.add_edge("wait_for_source_review", END)
    graph.add_edge("upload_source", "source_review_gate")
    graph.add_edge("prepare_search_more", "generate_report_queries")
    graph.add_edge("halt_report", END)

    # Indexing
    graph.add_edge("accept_candidates", "index_sources")
    graph.add_edge("index_sources", "retrieve")

    # Retrieval / reranking
    graph.add_conditional_edges(
        "retrieve",
        should_rerank,
        {
            "rerank": "rerank",
            "build_context_pack": "build_context_pack",
        },
    )

    graph.add_edge("rerank", "build_context_pack")

    # Writing
    graph.add_edge("build_context_pack", "plan_sections")
    graph.add_edge("plan_sections", "write_sections")
    graph.add_edge("write_sections", "qa")

    # QA
    graph.add_conditional_edges(
        "qa",
        route_after_qa,
        {
            "passed": "final_review_gate",
            "failed": "prepare_revision",
        },
    )

    graph.add_edge("prepare_revision", "write_sections")

    # Final review
    graph.add_conditional_edges(
        "final_review_gate",
        route_after_final_review,
        {
            "approve": "export_report",
            "request_changes": "prepare_revision",
            "wait": "wait_for_final_review",
            "halt": "halt_report",
        },
    )

    graph.add_edge("wait_for_final_review", END)
    graph.add_edge("export_report", "complete_report")
    graph.add_edge("complete_report", END)

    return graph.compile()


rag_graph = build_rag_graph()
report_graph = build_report_graph()
