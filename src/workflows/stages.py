"""파이프라인 단계 함수 — research / write / assemble.

- research: 자료 수집(현재 스텁). 향후 web_research/indexing 서브그래프로 교체.
- write:    섹션별 검색→후보 생성→정적 게이트. run_write_loop 위임 + QA_SELECT 게이트에서 정지.
- assemble: 사람이 고른 후보를 조립하고 보고서 레벨 정적검사(structure_complete) 실행.

write의 검색기·LLM은 모듈 전역(_retriever_factory·_write_client)으로 주입식 — 테스트는
이를 fake로 교체해 실검색/실LLM 없이 파이프라인을 관통시킨다. 실제 LLM 호출은
run_write_loop 내부에서 token_context로 감싸진다.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import structlog

from src.clients.llm.base import LLMClient
from src.core.state import ProjectState
from src.core.types import SectionPlan, SourceRef, SourceType
from src.services.retrieval.section import SectionRetriever
from src.workflows.write_loop import check_assembled, run_write_loop

logger = structlog.get_logger(__name__)


async def research(state: ProjectState) -> ProjectState:
    """자료 수집(스텁). 실제로는 검색·인덱싱 서브그래프를 돈다."""
    # TODO(seam): web_research.WebResearchService().collect(spec) + indexing으로 교체
    sources = [
        SourceRef(
            id=uuid4(),
            source_type=SourceType.WEB_SEARCH,
            title=f"{state.topic} 관련 정부자료 A",
            url="https://example.org/a",
        ),
        SourceRef(
            id=uuid4(),
            source_type=SourceType.LIBRARY,
            title=f"{state.topic} 관련 학술자료 B",
        ),
    ]
    return state.add_sources(sources)


def _ensure_section_plan(state: ProjectState) -> ProjectState:
    """섹션 계획이 없으면 임시 계획 생성.

    TODO(planner): outline(목차) 기반 섹션 플래너로 교체. 지금은 상류(planning) 미배선이라
    최소 2섹션 placeholder로 write 루프를 관통시킨다.
    """
    if state.section_plan:
        return state
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
    from src.clients.embedding_client import BgeM3Client
    from src.db.session import async_session_maker
    from src.services.retrieval import HybridSearchClient
    from src.services.retrieval._keyword import KeywordSearchClient
    from src.services.retrieval._semantic import SemanticSearchClient
    from src.services.retrieval.section import make_section_retriever

    embedder = BgeM3Client()
    semantic = SemanticSearchClient(async_session_maker, embedder)
    keyword = KeywordSearchClient(async_session_maker)
    hybrid = HybridSearchClient(semantic, keyword)
    return make_section_retriever(hybrid, state.project_id)


# 주입 지점 — 테스트는 이 두 전역을 fake로 교체한다.
_retriever_factory: Callable[[ProjectState], SectionRetriever] = _default_retriever_factory
_write_client: LLMClient | None = None


async def write(state: ProjectState) -> ProjectState:
    """섹션별 후보 생성 + 정적 게이트. 결과를 state.section_candidates에 적재.

    이후 파이프라인이 QA_SELECT 게이트에서 정지하고 사람이 고른다.
    """
    state = _ensure_section_plan(state)
    retrieve = _retriever_factory(state)
    return await run_write_loop(state, retrieve=retrieve, client=_write_client)


async def assemble(state: ProjectState) -> ProjectState:
    """사람이 고른 후보를 조립하고 보고서 레벨 정적검사를 실행.

    structure_complete 실패(누락 섹션)는 로깅한다 — 향후 FINAL 게이트로 되돌리는 지점.
    TODO(hwpx): 조립된 draft를 HWPX로 렌더.
    """
    drafts, result = check_assembled(state)
    logger.info(
        "assemble.done",
        project_id=str(state.project_id),
        selected=len(drafts),
        structure_ok=result.passed,
        detail=result.detail,
    )
    return state
