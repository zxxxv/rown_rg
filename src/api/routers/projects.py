from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.schemas.execution import DecideRequest, ProgressResponse, RunResponse
from src.api.schemas.project import ConfigUpdateRequest, PresetRead, ProjectCreate, ProjectRead
from src.core.config import settings
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.types import ProjectStage
from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.prompts import list_presets, load_preset
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


async def _get_authorized_project(
    project_id: UUID,
    session: AsyncSession,
    current_user: User,
) -> Project:
    """프로젝트 로드 + 소유자/관리자 권한 확인."""
    project = await session.get(Project, project_id)
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
    await session.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    q: str | None = None,
) -> list[Project]:
    """내 프로젝트 목록(최신순). admin·super_admin은 전체를 본다.

    status=단계 필터(ProjectStage 값), q=제목·주제 부분 검색.
    """
    if status is not None and status not in _STAGE_PERCENT:
        raise ValidationError(
            message=f"알 수 없는 status: {status} (가능: {', '.join(_STAGE_PERCENT)})",
            code="INVALID_STATUS_FILTER",
        )
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
    if current_user.role not in ("super_admin", "admin"):
        stmt = stmt.where(Project.owner_id == current_user.id)
    if status is not None:
        stmt = stmt.where(Project.status == status)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Project.title.ilike(pattern), Project.topic.ilike(pattern)))
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
    # 스켈레톤: 새로 생성된 프로젝트만 실행(thread_id=project_id 재사용 충돌 회피).
    if project.status != ProjectStage.CREATED.value:
        raise ValidationError(
            message=f"실행할 수 없는 상태입니다(현재: {project.status})",
            code="PROJECT_NOT_RUNNABLE",
        )
    # 백그라운드 실행 시작 — 즉시 반환. 사용자 세션과 분리되어 진행된다.
    await start_run(project.id)
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
    project.config = data.config
    await session.flush()
    await session.refresh(project)
    return project


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
