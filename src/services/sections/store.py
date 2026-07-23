"""완료 섹션 영구 저장 — assemble 시점에 selected_drafts를 sections 테이블에 적재.

ProjectState는 인메모리 작업사본이라 실행이 끝나면 사라진다. 이 모듈이 그 순간의
확정 본문을 정규 테이블로 옮겨, 사후 조회·편집(GET/PATCH /sections)의 유일한 원천이 된다.
프로젝트당 전량 교체(delete-all → insert) — 재실행 시 stale 행이 남지 않는다.
"""

from __future__ import annotations

import structlog
from sqlalchemy import delete

from src.core.state import ProjectState
from src.core.types import SectionCandidateSet
from src.db.models.section import Section
from src.db.session import async_session_maker
from src.prompts import load_preset

logger = structlog.get_logger(__name__)


def _chapter_titles(state: ProjectState) -> dict[int, str]:
    """chapter_number → 챕터 제목. 프리셋이 있으면 위치로 매핑, 없으면 'N장' 폴백.

    planner._assign_analysts와 동일한 위치 대응 관례(챕터 순서=프리셋 순서)를 따른다.
    """
    titles: dict[int, str] = {}
    if state.preset:
        try:
            preset = load_preset(state.preset)
            for i, ch in enumerate(preset.chapters, start=1):
                titles[i] = ch.title
        except KeyError:
            pass
    # 프리셋 밖 챕터·자유 주제는 폴백
    for plan in state.section_plan:
        titles.setdefault(plan.chapter_number, f"{plan.chapter_number}장")
    return titles


def _qa_status(section_id, selections, cand_sets: list[SectionCandidateSet]) -> str:
    """선택된 후보의 정적검사 결과 → qa_status. soft 경고 있으면 failed, 없으면 passed."""
    chosen = selections.get(section_id)
    if chosen is None:
        return "pending"
    for cset in cand_sets:
        if cset.section_id != section_id:
            continue
        for cand in cset.candidates:
            if cand.candidate_id == chosen:
                return "failed" if cand.report.warnings else "passed"
    return "pending"


def _rows(state: ProjectState) -> list[Section]:
    drafts = {d.section_id: d for d in state.selected_drafts()}
    chapter_titles = _chapter_titles(state)
    rows: list[Section] = []
    for plan in state.section_plan:
        draft = drafts.get(plan.section_id)
        rows.append(
            Section(
                id=plan.section_id,
                project_id=state.project_id,
                chapter_number=plan.chapter_number,
                section_number=plan.section_number,
                chapter_title=chapter_titles.get(plan.chapter_number, f"{plan.chapter_number}장"),
                title=plan.title,
                level=2,
                content=draft.content if draft is not None else "",
                source_ids=list(draft.cited_chunk_ids) if draft is not None else [],
                qa_status=_qa_status(
                    plan.section_id, state.section_selections, state.section_candidates
                ),
                status="completed" if draft is not None else "pending",
            )
        )
    return rows


async def persist_sections(state: ProjectState) -> None:
    """selected_drafts를 sections 테이블로 전량 교체 저장(프로젝트 스코프).

    요청 밖 경로라 자체 세션을 열고(=opener) 직접 커밋한다(session.py 규칙).
    """
    rows = _rows(state)
    async with async_session_maker() as session:
        await session.execute(delete(Section).where(Section.project_id == state.project_id))
        session.add_all(rows)
        await session.commit()
    logger.info("sections.persisted", project_id=str(state.project_id), n=len(rows))
