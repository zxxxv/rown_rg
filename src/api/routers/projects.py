from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.schemas.execution import DecideRequest, ProgressResponse, RunResponse
from src.api.schemas.project import ProjectCreate, ProjectRead
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.types import ProjectStage
from src.db.models.project import Project
from src.db.models.user import User
from src.workflows.runner import get_pending_gate, resume_run, start_run

router = APIRouter(prefix="/projects", tags=["projects"])


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
    return ProgressResponse(
        project_id=str(project.id),
        status=ProjectStage(project.status),
        pending_gate=await get_pending_gate(session, project.id),
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
