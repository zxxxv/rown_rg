"""파이프라인 단계 함수 — research / write.

이 두 자리가 향후 **LangGraph 서브그래프가 들어갈 seam**이다(사이클이 생길 때):
  · research = 쿼리 병렬 + (자료 부족/모순 시) 재검색 루프
  · write    = 섹션 병렬 + Writer↔Critic QA 루프
단순 병렬이면 asyncio.gather로 충분하고, 사이클이 필요해질 때만 LangGraph로 올린다.
실제 LLM 호출은 token_context(user_id=owner, project_id, operation)로 감싼다.

지금은 결정적 스텁 — 척추(pipeline) 루프를 검증하기 위한 최소 구현이다.
각 함수는 ProjectState를 받아 갱신된 ProjectState를 돌려주는 순수 변환이다.
"""

from __future__ import annotations

from uuid import uuid4

from src.core.state import ProjectState
from src.core.types import SectionPlan, SourceRef, SourceType


async def research(state: ProjectState) -> ProjectState:
    """자료 수집(스텁). 실제로는 검색·인덱싱 서브그래프를 돈다."""
    # TODO(LangGraph seam): token_context로 감싼 검색 서브그래프로 교체
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


async def write(state: ProjectState) -> ProjectState:
    """초안 작성(스텁). 실제로는 섹션별 Writer 서브그래프를 돈다."""
    # TODO(LangGraph seam): 섹션 병렬 + Writer↔Critic 루프로 교체
    plan = [
        SectionPlan(chapter_number=1, section_number=1, title="개요"),
        SectionPlan(chapter_number=2, section_number=1, title="분석"),
    ]
    state = state.with_section_plan(plan)
    for section in plan:
        state = state.mark_section_complete(section.section_id)
    return state
