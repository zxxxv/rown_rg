"""프로젝트 단계를 **산출물에서 되짚는다** — status가 두 가지를 뜻하던 병을 끊는다.

**병리**: projects.status 하나가 "어디까지 왔나"(진척)와 "지금 뭐가 도나"(실행)를
겸했다. 러너가 도는 동안은 맞는 말이지만, 멈춘 뒤에도 그 값이 남아 화면이 "AI가 자료를
검색하고 있습니다"를 스피너와 함께 띄운다 — 아무것도 안 도니 끝나지도 않는다. 실제
사고가 여기서 났다(2026-08-25 재개 보고). 그때는 게이트를 함께 열어 화면을 검토 모드로
돌리는 것으로 덮었는데, 덮개지 치료가 아니었다.

**치료**: 두 사실을 갈라 놓는다.
- **실행**: 러너/작업 등록부가 답한다(is_running). 컬럼이 아니다.
- **진척**: 산출물이 답한다 — 브리프가 있나, 자료가 있나, 색인이 됐나, 본문이
  쓰였나, 사람이 확정했나. 전부 DB에 남는 사실이라 되짚을 수 있다.

컬럼은 **파생값의 캐시**로 남긴다. 지우지 않는 이유는 목록 필터(SQL WHERE)가 행마다
파생을 돌릴 수 없어서다. 대신 전이 지점마다 sync_project_stage로 다시 계산해 넣어,
컬럼이 거짓말을 하지 않게 한다.

돌고 있을 때는 러너가 쓴 값을 그대로 믿는다 — 지금 어느 단계인지 아는 유일한 주체다.
멈춰 있을 때만 산출물로 되짚는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.types import ProjectStage
from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section

logger = structlog.get_logger(__name__)

# 사람이 명시적으로 만든 사실 — 산출물로는 되짚을 수 없어 컬럼이 정본이다.
TERMINAL_STAGES = (ProjectStage.ARCHIVED.value, ProjectStage.CANCELLED.value)


@dataclass(frozen=True)
class ProjectFacts:
    """단계를 되짚는 데 필요한 산출물 사실 — 전부 DB에 남는 것들."""

    has_brief: bool
    n_sources: int
    n_chunks: int
    n_sections: int
    n_written: int
    finalized: bool


async def collect_facts(session: AsyncSession, project: Project) -> ProjectFacts:
    """산출물 사실 한 벌 — 세 번의 count로 끝난다(상세 화면이 이미 이만큼 읽는다)."""
    n_sources = (
        await session.execute(
            select(func.count())
            .select_from(ProjectSource)
            .where(ProjectSource.project_id == project.id)
        )
    ).scalar_one()
    n_chunks = (
        await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.project_id == project.id)
        )
    ).scalar_one()
    n_sections, n_written = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(func.length(func.trim(Section.content)) > 0),
            ).where(Section.project_id == project.id)
        )
    ).one()
    config = project.config or {}
    return ProjectFacts(
        # 설계 브리프는 config에 산다(생성 폼이 채우거나 AI가 쓴다).
        has_brief=bool(config.get("design_brief") or config.get("outline")),
        n_sources=int(n_sources or 0),
        n_chunks=int(n_chunks or 0),
        n_sections=int(n_sections or 0),
        n_written=int(n_written or 0),
        finalized=project.completed_at is not None,
    )


def derive_stage(project: Project, facts: ProjectFacts, *, running: bool) -> ProjectStage:
    """지금 이 프로젝트가 서 있는 단계 (순수 함수).

    우선순위가 곧 신뢰의 순서다:
    1. 사람이 끝낸 것(보관·취소)은 되짚을 수 없다 — 컬럼이 정본.
    2. 돌고 있으면 러너가 쓴 값 — 지금 어디인지 아는 유일한 주체.
    3. 확정(completed_at)했으면 완료. 다시 열면 이 값이 풀리므로 저절로 아래로 내려간다.
    4. 그 밖에는 산출물이 어디까지 왔는지가 답이다.

    멈춰 있을 때 돌려주는 값은 "다음에 할 일이 있는 자리"다 — 본문이 있으면 검토,
    색인만 됐으면 작성, 자료만 있으면 자료, 아무것도 없으면 시작 전.
    """
    if project.status in TERMINAL_STAGES:
        return ProjectStage(project.status)
    if running:
        try:
            return ProjectStage(project.status)
        except ValueError:  # 알 수 없는 값 — 산출물로 되짚는다
            pass
    if facts.finalized:
        return ProjectStage.COMPLETED
    if facts.n_written > 0:
        # 본문이 있는데 확정 전 = 사람이 볼 차례다. 다시 열기가 정확히 이 자리로 온다.
        return ProjectStage.REVIEWING
    if facts.n_chunks > 0:
        return ProjectStage.WRITING
    if facts.n_sources > 0:
        return ProjectStage.RESEARCHING
    if facts.has_brief:
        return ProjectStage.PLANNING
    return ProjectStage.CREATED


async def sync_project_stage(
    session: AsyncSession, project: Project, *, running: bool = False
) -> bool:
    """파생값을 컬럼에 다시 새긴다 — 전이 지점에서 부른다. 돌려주는 값은 "고쳤는가".

    컬럼을 남겨 두는 유일한 이유가 목록 필터(SQL WHERE)라, 그 필터가 거짓말을 하지
    않으려면 전이마다 다시 계산해 넣어야 한다. 값이 그대로면 아무것도 쓰지 않는다.

    고쳤는지를 돌려주는 이유: flush가 updated_at(onupdate)을 만료시켜, 호출부가 그
    객체를 그대로 직렬화하면 비동기 밖에서 IO를 시도한다(MissingGreenlet). 되읽어야
    할 때를 호출부가 알아야 한다.
    """
    facts = await collect_facts(session, project)
    stage = derive_stage(project, facts, running=running)
    if project.status == stage.value:
        return False
    logger.info(
        "project.stage_synced",
        project_id=str(project.id),
        was=project.status,
        now=stage.value,
    )
    project.status = stage.value
    await session.flush()
    return True


async def derived_stage_for(
    session: AsyncSession, project: Project, *, running: bool
) -> ProjectStage:
    """읽기 경로용 — 컬럼을 고치지 않고 파생값만 계산한다(옛 행 자가 치유)."""
    return derive_stage(project, await collect_facts(session, project), running=running)


async def stage_of(session: AsyncSession, project_id: UUID) -> ProjectStage | None:
    """id만 있을 때의 편의 진입점 — 없으면 None."""
    project = await session.get(Project, project_id)
    if project is None:
        return None
    from src.workflows.runner import is_running

    return await derived_stage_for(session, project, running=is_running(project_id))
