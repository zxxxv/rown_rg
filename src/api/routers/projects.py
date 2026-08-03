from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.schemas.execution import DecideRequest, ProgressResponse, RunResponse
from src.api.schemas.project import (
    AnalystRead,
    ConfigUpdateRequest,
    OutlineIn,
    PresetChapterRead,
    PresetDetailRead,
    PresetRead,
    PresetSectionRead,
    ProjectCreate,
    ProjectRead,
    VerifyFindingRead,
)
from src.api.schemas.section import (
    ChapterNode,
    SectionContentResponse,
    SectionContentUpdate,
    SectionNode,
    SectionRewriteRequest,
    SectionTreeResponse,
)
from src.core.config import settings
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.state import ProjectState
from src.core.types import ProjectStage, SectionDraft, SectionPlan
from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.verify_finding import VerifyFinding
from src.prompts import list_presets, load_preset
from src.services.generation.planner import MAX_SECTIONS
from src.services.prompts import resolve_analysts
from src.workflows.runner import get_pending_gate, resume_run, start_run

# 단계 기반 근사 진행률 — 섹션 단위 세밀화는 추후 진행 이벤트로
_STAGE_PERCENT: dict[str, int] = {
    ProjectStage.CREATED.value: 0,
    ProjectStage.RESEARCHING.value: 20,
    ProjectStage.INDEXING.value: 40,
    ProjectStage.WRITING.value: 60,
    ProjectStage.REVIEWING.value: 85,
    ProjectStage.COMPLETED.value: 100,
    ProjectStage.ARCHIVED.value: 100,
}

router = APIRouter(prefix="/projects", tags=["projects"])

# 생성 화면 프리셋 드롭다운용 — 카탈로그(src.prompts)가 단일 진실.
presets_router = APIRouter(prefix="/presets", tags=["presets"])

# 섹션별 담당 에이전트 배정 UI용 — 역시 파일 카탈로그가 단일 진실.
analysts_router = APIRouter(prefix="/analysts", tags=["analysts"])


def _validate_outline_config(config: dict, known_analysts: set[str]) -> None:
    """config.outline이 있으면 형태·섹션 수·에이전트 이름을 검증한다.

    outline은 planner LLM을 우회해 그대로 실행되므로 생성/수정 시점에 막는 게
    마지막 방어선이다. known_analysts는 개인→시스템 병합 카탈로그의 id·name 집합
    (개인 에이전트도 배정 가능하도록 호출부에서 resolve_analysts로 계산해 넘긴다).
    """
    outline = config.get("outline")
    if outline is None:
        return
    try:
        parsed = OutlineIn.model_validate(outline)
    except PydanticValidationError as e:
        raise ValidationError(
            message=f"outline 형식이 올바르지 않습니다: {e.errors()[0].get('msg', '')}",
            code="INVALID_OUTLINE",
        ) from None
    sections = [s for ch in parsed.chapters for s in ch.sections]
    if not sections:
        raise ValidationError(message="outline에 섹션이 없습니다", code="INVALID_OUTLINE")
    if len(sections) > MAX_SECTIONS:
        raise ValidationError(
            message=f"섹션이 너무 많습니다: {len(sections)}개 (최대 {MAX_SECTIONS})",
            code="OUTLINE_TOO_LARGE",
        )
    unknown = sorted({name for s in sections for name in s.analysts} - known_analysts)
    if unknown:
        raise ValidationError(
            message=f"알 수 없는 분석 에이전트: {', '.join(unknown)}",
            code="UNKNOWN_ANALYST",
        )


async def _known_analyst_names(session: AsyncSession, owner_id: UUID) -> set[str]:
    """개인→시스템 병합 에이전트의 id·name 집합(outline 검증용)."""
    known: set[str] = set()
    for a in await resolve_analysts(session, owner_id):
        known.add(a.id)
        known.add(a.name)
    return known


@presets_router.get("", response_model=list[PresetRead])
async def get_presets(
    _: Annotated[User, Depends(get_current_active_user)],
) -> list[PresetRead]:
    """보고서 유형 프리셋 카탈로그. 자유 주제는 preset=None으로 생성하면 된다."""
    return [
        PresetRead(
            id=p.id,
            name=p.name,
            desc=p.desc,
            n_chapters=len(p.chapters),
            n_sections=sum(len(ch.sections) for ch in p.chapters),
        )
        for p in list_presets()
    ]


@presets_router.get("/{preset_key}", response_model=PresetDetailRead)
async def get_preset_detail(
    preset_key: str,
    _: Annotated[User, Depends(get_current_active_user)],
) -> PresetDetailRead:
    """프리셋 전체 골격(챕터·섹션·방향·핵심포인트·담당 에이전트).

    생성 화면에서 프리셋을 클릭해 들어가면 이 골격이 목차 편집기의 초기값이 된다.
    """
    try:
        p = load_preset(preset_key)
    except KeyError:
        raise NotFoundError(message="프리셋을 찾을 수 없습니다", code="PRESET_NOT_FOUND") from None
    return PresetDetailRead(
        id=p.id,
        name=p.name,
        desc=p.desc,
        domain_context=p.domain_context,
        chapters=[
            PresetChapterRead(
                title=ch.title,
                sections=[
                    PresetSectionRead(
                        title=s.title,
                        direction=s.direction,
                        key_points=list(s.key_points),
                        agents=list(s.agents),
                    )
                    for s in ch.sections
                ],
            )
            for ch in p.chapters
        ],
    )


@analysts_router.get("", response_model=list[AnalystRead])
async def get_analysts(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[AnalystRead]:
    """분석 에이전트 카탈로그 — 섹션별 담당 배정 UI의 선택지.

    개인→시스템 병합 목록(개인 에이전트가 있으면 함께/덮어써서 노출).
    """
    return [
        AnalystRead(
            id=a.id,
            name=a.name,
            cat=a.cat,
            desc=a.desc,
            pages=a.volume_target.pages if a.volume_target else None,
        )
        for a in await resolve_analysts(session, current_user.id)
    ]


async def _get_authorized_project(
    project_id: UUID,
    session: AsyncSession,
    current_user: User,
) -> Project:
    """프로젝트 로드 + 소유자/관리자 권한 확인."""
    project = await session.get(Project, project_id, options=[selectinload(Project.owner)])
    if project is None:
        raise NotFoundError(message="프로젝트를 찾을 수 없습니다", code="PROJECT_NOT_FOUND")
    if project.owner_id != current_user.id and current_user.role not in ("super_admin", "admin"):
        raise AuthorizationError(message="권한이 없습니다", code="FORBIDDEN")
    return project


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Project:
    # preset은 파일 카탈로그(src.prompts)가 단일 진실 — 생성 시점에 키를 검증해 확정한다.
    # None이면 자유 주제(프리셋 없는 일반 목차 설계)로 진행된다.
    if data.preset is not None:
        try:
            load_preset(data.preset)
        except KeyError:
            available = ", ".join(p.name for p in list_presets())
            raise ValidationError(
                message=f"알 수 없는 프리셋입니다: {data.preset} (가능: {available})",
                code="UNKNOWN_PRESET",
            ) from None
    # 사용자 확정 목차가 실려 있으면 여기서 검증해 확정한다 (실행 시점 실패 방지).
    _validate_outline_config(data.config, await _known_analyst_names(session, current_user.id))
    project = Project(
        title=data.title,
        topic=data.topic,
        preset=data.preset,
        config=data.config,
        depth_mode=data.depth_mode,
        owner_id=current_user.id,
        status=ProjectStage.CREATED.value,
    )
    session.add(project)
    await session.flush()
    await session.refresh(project, ["owner"])
    return project


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    q: str | None = None,
    scope: str = "mine",
) -> list[Project]:
    """프로젝트 목록(최신순).

    scope=mine(기본)은 내 것만, scope=all은 전체 — 단 all은 admin·super_admin만 허용
    (일반 사용자는 scope와 무관하게 항상 자기 것). status=단계 필터, q=제목·주제·소유자명 검색.
    """
    if status is not None and status not in _STAGE_PERCENT:
        raise ValidationError(
            message=f"알 수 없는 status: {status} (가능: {', '.join(_STAGE_PERCENT)})",
            code="INVALID_STATUS_FILTER",
        )
    if scope not in ("mine", "all"):
        raise ValidationError(message="scope는 mine 또는 all 이어야 합니다", code="INVALID_SCOPE")
    is_admin = current_user.role in ("super_admin", "admin")

    stmt = select(Project).options(selectinload(Project.owner))
    # 가시성: 일반 사용자는 항상 자기 것. 관리자는 scope=all일 때만 전체.
    if not (is_admin and scope == "all"):
        stmt = stmt.where(Project.owner_id == current_user.id)
    if status is not None:
        stmt = stmt.where(Project.status == status)
    if q:
        pattern = f"%{q}%"
        # 제목·주제·소유자명 검색(소유자명은 owner join으로).
        stmt = stmt.join(User, Project.owner_id == User.id).where(
            or_(
                Project.title.ilike(pattern),
                Project.topic.ilike(pattern),
                User.name.ilike(pattern),
            )
        )
    stmt = stmt.order_by(Project.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Project:
    return await _get_authorized_project(project_id, session, current_user)


@router.post("/{project_id}/run", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RunResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    # 새로 생성된 프로젝트만 실행(thread_id=project_id 재사용 충돌 회피).
    # 주의: 여기서 status를 미리 researching으로 바꾸면 안 된다 — 상태는 척추의
    # 진행 위치라서 선점하면 _execute가 research 단계를 건너뛴다(2026-08-03 실사고:
    # 자료 0건·게이트 없이 폴백 목차로 완료). 중복 기동 차단은 runner의
    # 인프로세스 가드(_RUNNING, 단일 워커 전제)가 담당한다.
    if project.status != ProjectStage.CREATED.value:
        raise ValidationError(
            message=f"실행할 수 없는 상태입니다(현재: {project.status})",
            code="PROJECT_NOT_RUNNABLE",
        )
    if not await start_run(project.id):
        raise ValidationError(
            message="이미 실행 중인 프로젝트입니다",
            code="ALREADY_RUNNING",
        )
    return RunResponse(project_id=str(project.id), status=ProjectStage.RESEARCHING)


@router.get("/{project_id}/progress", response_model=ProgressResponse)
async def get_progress(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ProgressResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    usage = (
        await session.execute(
            select(
                func.coalesce(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0),
            ).where(TokenUsage.project_id == project.id)
        )
    ).one()
    return ProgressResponse(
        project_id=str(project.id),
        status=ProjectStage(project.status),
        pending_gate=await get_pending_gate(session, project.id),
        percent=_STAGE_PERCENT.get(project.status, 0),
        tokens_used=int(usage[0]),
        cost_usd=float(usage[1]),
    )


@router.patch("/{project_id}/config", response_model=ProjectRead)
async def update_project_config(
    project_id: UUID,
    data: ConfigUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> Project:
    """진행 중 프로젝트의 옵션(config) 전체 교체.

    다음 단계부터 반영된다(이미 지나간 단계는 재실행하지 않음). 제목·주제는
    생성 후 변경 불가(프론트 edit 모드도 readonly).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    _validate_outline_config(data.config, await _known_analyst_names(session, current_user.id))
    project.config = data.config
    await session.flush()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """완료(completed·archived)된 프로젝트 영구 삭제.

    하위 데이터(sources·chunks·raptor·consistency·review_points)는 DB FK CASCADE로
    함께 삭제되고, token_usage는 SET NULL이라 사용량·비용 기록은 남는다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if project.status not in (ProjectStage.COMPLETED.value, ProjectStage.ARCHIVED.value):
        raise ValidationError(
            message=f"완료된 프로젝트만 삭제할 수 있습니다(현재: {project.status})",
            code="PROJECT_NOT_DELETABLE",
        )
    # ORM 관계는 lazy="raise"라 session.delete()의 관계 로딩을 피하고
    # DB FK CASCADE에 맡기는 Core DELETE를 쓴다.
    await session.execute(delete(Project).where(Project.id == project.id))


@router.get("/{project_id}/verify-report", response_model=list[VerifyFindingRead])
async def get_verify_report(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[VerifyFinding]:
    """PM 검증 경고 리포트 — assemble 직후 챕터당 1콜 pm_verify의 결과.

    차단이 아닌 참고용: 절 간 수치·용어 충돌, 법령 시점 상충(critical),
    챕터 간 통계 중복 등 문서 횡단 문제를 사람이 편집기에서 판단한다.
    아직 검증 전이거나 경고가 없으면 빈 배열.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    rows = (
        await session.execute(
            select(VerifyFinding)
            .where(VerifyFinding.project_id == project.id)
            .order_by(VerifyFinding.chapter_number, VerifyFinding.created_at)
        )
    ).scalars()
    return list(rows)


@router.get("/{project_id}/export")
async def download_export(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FileResponse:
    """완성된 보고서 HWPX 다운로드.

    assemble이 <export_dir>/<project_id>.hwpx 결정적 경로에 렌더하므로 상태 기록
    없이 경로 규칙만으로 찾는다. 파일이 없으면(미완료·렌더 스킵) 404.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    path = Path(settings.export_dir) / f"{project.id}.hwpx"
    if not path.is_file():
        raise NotFoundError(
            message="보고서 파일이 아직 없습니다 (작성 미완료이거나 렌더되지 않음)",
            code="EXPORT_NOT_READY",
        )
    return FileResponse(
        path,
        filename=f"{project.title}.hwpx",
        media_type="application/octet-stream",
    )


@router.post("/{project_id}/decide", response_model=RunResponse)
async def decide_gate(
    project_id: UUID,
    data: DecideRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> RunResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    if await get_pending_gate(session, project.id) is None:
        raise ValidationError(message="대기 중인 검토 게이트가 없습니다", code="NO_PENDING_GATE")
    # 결정값으로 척추 재개 — 백그라운드. 게이트 다음 단계부터 이어서 진행된다.
    await resume_run(project.id, data.decision)
    return RunResponse(project_id=str(project.id), status=ProjectStage.RESEARCHING)


# ═══════════════════════════════════════════════════════════════════════════
# 섹션 조회·편집 — sections 테이블(assemble 시 영구 저장)이 원천.
# 프론트 미리보기/편집 화면 계약(web/src/api/sections.ts·types.ts)과 1:1.
# ═══════════════════════════════════════════════════════════════════════════


def _reduce_chapter_status(statuses: list[str]) -> str:
    """섹션 상태들 → 챕터 상태. 전부 완료면 completed, 하나라도 진행/완료면 writing,
    전부 실패면 failed, 그 외 pending."""
    if not statuses:
        return "pending"
    if all(s == "completed" for s in statuses):
        return "completed"
    if any(s in ("completed", "writing") for s in statuses):
        return "writing"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "pending"


async def _load_sections(session: AsyncSession, project_id: UUID) -> list[Section]:
    stmt = (
        select(Section)
        .where(Section.project_id == project_id)
        .order_by(Section.chapter_number, Section.section_number)
    )
    return list((await session.execute(stmt)).scalars())


@router.get("/{project_id}/sections", response_model=SectionTreeResponse)
async def get_sections(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionTreeResponse:
    """섹션 트리(장 → 절). 장 노드는 절들을 묶어 합성하고 상태는 하위에서 유도한다."""
    project = await _get_authorized_project(project_id, session, current_user)
    rows = await _load_sections(session, project.id)

    chapters: dict[int, ChapterNode] = {}
    chapter_statuses: dict[int, list[str]] = {}
    for row in rows:
        ch_id = f"ch-{row.chapter_number}"
        if row.chapter_number not in chapters:
            chapters[row.chapter_number] = ChapterNode(
                id=ch_id, title=row.chapter_title, level=1, status="pending", children=[]
            )
            chapter_statuses[row.chapter_number] = []
        chapters[row.chapter_number].children.append(
            SectionNode(
                id=str(row.id),
                title=row.title,
                level=row.level,
                status=row.status,
                parent_id=ch_id,
            )
        )
        chapter_statuses[row.chapter_number].append(row.status)

    tree = [chapters[n] for n in sorted(chapters)]
    for n, node in zip(sorted(chapters), tree, strict=True):
        node.status = _reduce_chapter_status(chapter_statuses[n])
    return SectionTreeResponse(tree=tree)


async def _get_section(session: AsyncSession, project_id: UUID, section_id: UUID) -> Section:
    row = await session.get(Section, section_id)
    if row is None or row.project_id != project_id:
        raise NotFoundError(message="섹션을 찾을 수 없습니다", code="SECTION_NOT_FOUND")
    return row


def _section_content(row: Section) -> SectionContentResponse:
    return SectionContentResponse(
        id=str(row.id),
        title=row.title,
        content=row.content,
        source_ids=[str(s) for s in row.source_ids],
        qa_status=row.qa_status,
        level=row.level,
    )


@router.get("/{project_id}/sections/{section_id}", response_model=SectionContentResponse)
async def get_section_content(
    project_id: UUID,
    section_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionContentResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    return _section_content(await _get_section(session, project.id, section_id))


@router.patch("/{project_id}/sections/{section_id}", response_model=SectionContentResponse)
async def update_section_content(
    project_id: UUID,
    section_id: UUID,
    data: SectionContentUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionContentResponse:
    """수동 편집 저장 — 본문 교체. 상태를 completed로 확정한다."""
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_section(session, project.id, section_id)
    row.content = data.content
    row.status = "completed"
    await session.flush()
    await session.refresh(row)
    return _section_content(row)


async def _default_section_rewriter(
    project: Project, plan: SectionPlan, instruction: str
) -> SectionDraft:
    """실검색+실LLM으로 한 섹션 재작성. write 파이프라인의 검색기·생성기를 재사용한다."""
    from src.services.sections.edit import regenerate_section
    from src.workflows.stages import _default_retriever_factory

    state = ProjectState(
        project_id=project.id,
        user_id=project.owner_id,
        topic=project.topic,
        preset=project.preset,
        options=project.config,
    )
    retrieve = _default_retriever_factory(state)
    return await regenerate_section(
        section=plan,
        retrieve=retrieve,
        instruction=instruction,
        user_id=project.owner_id,
        project_id=project.id,
    )


# 주입 지점 — 테스트는 이 전역을 fake로 교체한다(실검색·실LLM 회피).
_section_rewriter = _default_section_rewriter


@router.post("/{project_id}/sections/{section_id}/rewrite", response_model=SectionContentResponse)
async def rewrite_section(
    project_id: UUID,
    section_id: UUID,
    data: SectionRewriteRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionContentResponse:
    """AI 재작성 — 프로젝트 인덱스에서 근거를 검색해 이 섹션만 다시 쓴다.

    instruction으로 방향을 지시할 수 있다(빈 값이면 근거 기반 단순 재작성).
    결과는 sections 테이블에 저장되고 갱신된 본문을 반환한다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_section(session, project.id, section_id)
    plan = SectionPlan(
        section_id=row.id,
        chapter_number=row.chapter_number,
        section_number=row.section_number,
        title=row.title,
    )
    draft = await _section_rewriter(project, plan, data.instruction)
    row.content = draft.content
    row.source_ids = list(draft.cited_chunk_ids)
    row.status = "completed"
    row.qa_status = "passed"
    await session.flush()
    await session.refresh(row)
    return _section_content(row)
