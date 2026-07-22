"""파이프라인 단계 함수 — research / write / assemble.

- research: 목차 설계(플래너) → 웹 수집 → 청킹·임베딩 인덱싱. SOURCE_POOL 게이트 직전까지.
- write:    섹션별 검색→후보 생성→정적 게이트. run_write_loop 위임 + QA_SELECT 게이트에서 정지.
- assemble: 사람이 고른 후보를 조립하고 보고서 레벨 정적검사(structure_complete) 후 HWPX 렌더.

의존성은 모듈 전역 주입식(_plan_client·_research_service_factory·_web_indexer_factory·
_retriever_factory·_write_client·_exporter) — 테스트는 이를 fake로 교체해 실검색/실LLM/
실DB 없이 파이프라인을 관통시킨다. 실제 LLM 호출은 token_context로 감싸져 토큰이 귀속된다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import structlog

from src.clients.llm.base import LLMClient
from src.clients.llm.token_tracker import token_context
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionPlan, SourceRef, SourceType
from src.services.generation.planner import plan_sections
from src.services.indexing.web import WebSourceIndexer, build_web_source_indexer
from src.services.research import ResearchSpec, WebResearchService
from src.services.retrieval.section import SectionRetriever
from src.workflows.write_loop import check_assembled, run_write_loop

logger = structlog.get_logger(__name__)


async def research(state: ProjectState) -> ProjectState:
    """목차 설계 → 웹 수집 → 인덱싱.

    목차가 수집 질의(ResearchSpec.outline)의 입력이라 플래너가 먼저 돈다.
    수집된 웹 본문은 즉시 청킹·임베딩되어(web indexer) write의 검색 대상이 된다.
    본문 없는 출처도 자료 풀(project_sources·SourceRef)에는 남긴다 — 사람이
    SOURCE_POOL 게이트에서 전체 풀을 보고 판단할 수 있게.
    """
    if not state.section_plan:
        plan = await plan_sections(
            state.topic,
            state.preset or "blank",
            model=settings.planner_model,
            client=_plan_client,
            user_id=state.user_id,
            project_id=state.project_id,
        )
        state = state.with_section_plan(plan)

    spec = ResearchSpec(
        topic=state.topic,
        report_type=state.preset or "blank",
        outline=[s.title for s in state.section_plan],
    )
    with token_context(
        user_id=state.user_id, project_id=state.project_id, operation="research.collect"
    ):
        result = await _research_service_factory().collect(
            spec,
            model=settings.research_model,
            max_uses=settings.research_max_uses,
            max_tokens=settings.research_max_tokens,
        )
    if result.coverage_gaps:
        logger.warning(
            "research.coverage_gaps",
            project_id=str(state.project_id),
            gaps=result.coverage_gaps,
        )

    indexer = _web_indexer_factory()
    refs: list[SourceRef] = []
    indexed: list[UUID] = []
    for src in result.sources:
        indexed_result = await indexer.index(
            project_id=state.project_id,
            content_md=src.content_md or "",
            url=src.url,
            title=src.title,
            reliability=src.reliability,
        )
        refs.append(
            SourceRef(
                id=indexed_result.source_id,
                source_type=SourceType.WEB_SEARCH,
                title=src.title or src.url,
                url=src.url,
            )
        )
        if indexed_result.chunks_created:
            indexed.append(indexed_result.source_id)
    logger.info(
        "research.done",
        project_id=str(state.project_id),
        n_sources=len(refs),
        n_indexed=len(indexed),
    )
    return state.add_sources(refs).mark_indexed(indexed)


def _ensure_section_plan(state: ProjectState) -> ProjectState:
    """섹션 계획이 없으면 최소 계획으로 폴백.

    정상 경로는 research의 플래너가 계획을 만든다. 이 폴백은 계획 없이 write에
    진입한 비정상 흐름(레거시 데이터·테스트)에서 루프를 관통시키는 안전망이다.
    """
    if state.section_plan:
        return state
    logger.warning("write.plan_fallback", project_id=str(state.project_id))
    plan = [
        SectionPlan(chapter_number=1, section_number=1, title="개요"),
        SectionPlan(chapter_number=2, section_number=1, title="분석"),
    ]
    return state.with_section_plan(plan)


def _default_retriever_factory(state: ProjectState) -> SectionRetriever:
    """실검색 retriever — 프로젝트 인덱스 대상 hybrid 검색에 바인딩.

    lazy import로 모듈 로드를 가볍게 유지(임베딩 모델 로딩 회피). research/index가
    아직 미배선이면 인덱스가 비어 빈 결과가 나올 수 있다(구조는 정상).
    """
    from src.clients.embedding_factory import get_embedding_client
    from src.db.session import async_session_maker
    from src.services.retrieval import HybridSearchClient
    from src.services.retrieval._keyword import KeywordSearchClient
    from src.services.retrieval._semantic import SemanticSearchClient
    from src.services.retrieval.section import make_section_retriever

    embedder = get_embedding_client()
    expander = None
    if settings.hyde_enabled:
        from src.services.retrieval._hyde import make_hyde_expander

        expander = make_hyde_expander(
            model=settings.hyde_model, user_id=state.user_id, project_id=state.project_id
        )
    semantic = SemanticSearchClient(async_session_maker, embedder, query_expander=expander)
    keyword = KeywordSearchClient(async_session_maker)
    hybrid = HybridSearchClient(semantic, keyword)

    reranker = None
    if settings.reranker_enabled:
        from src.clients.reranker_factory import get_reranker_client

        reranker = get_reranker_client()
    return make_section_retriever(hybrid, state.project_id, reranker=reranker)


def _default_exporter(state: ProjectState) -> Path:
    """조립된 보고서를 HWPX로 렌더. lazy import로 hwpx 의존을 사용 시점으로 미룬다."""
    from src.services.export.report import export_report

    return export_report(state)


# 주입 지점 — 테스트는 이 전역들을 fake로 교체한다.
_plan_client: LLMClient | None = None
_research_service_factory: Callable[[], WebResearchService] = WebResearchService
_web_indexer_factory: Callable[[], WebSourceIndexer] = build_web_source_indexer
_retriever_factory: Callable[[ProjectState], SectionRetriever] = _default_retriever_factory
_write_client: LLMClient | None = None
_exporter: Callable[[ProjectState], Path] = _default_exporter


async def write(state: ProjectState) -> ProjectState:
    """섹션별 후보 생성 + 정적 게이트. 결과를 state.section_candidates에 적재.

    이후 파이프라인이 QA_SELECT 게이트에서 정지하고 사람이 고른다.
    """
    state = _ensure_section_plan(state)
    retrieve = _retriever_factory(state)
    return await run_write_loop(
        state, retrieve=retrieve, client=_write_client, model=settings.write_model
    )


async def assemble(state: ProjectState) -> ProjectState:
    """사람이 고른 후보를 조립·정적검사 후 HWPX 파일로 렌더.

    structure_complete 실패(누락 섹션)면 렌더를 건너뛰고 로깅한다 — 미완성 보고서를
    산출물로 내보내지 않는다(향후 FINAL 게이트로 사람에게 되돌릴 지점).
    """
    drafts, result = check_assembled(state)
    logger.info(
        "assemble.done",
        project_id=str(state.project_id),
        selected=len(drafts),
        structure_ok=result.passed,
        detail=result.detail,
    )
    if result.passed and drafts:
        path = _exporter(state)
        logger.info("assemble.exported", project_id=str(state.project_id), path=str(path))
    else:
        logger.warning(
            "assemble.export_skipped",
            project_id=str(state.project_id),
            detail=result.detail or "선택된 초안 없음",
        )
    return state
