from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.workflows.nodes.dependencies import (
    RagWorkflowDependencies,
    build_rag_dependencies,
)
from src.workflows.nodes.nodes import (
    build_context_pack_node,
    make_index_sources_node,
    make_rerank_node,
    make_retrieve_node,
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


rag_graph = build_rag_graph()
