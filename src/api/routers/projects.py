import re
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.schemas.execution import DecideRequest, ProgressResponse, RunResponse
from src.api.schemas.project import (
    AnalystRead,
    ConfigUpdateRequest,
    LibraryAttachRequest,
    OutlineIn,
    PresetChapterRead,
    PresetDetailRead,
    PresetRead,
    PresetSectionRead,
    ProjectCreate,
    ProjectRead,
    SourceIncludeUpdate,
    SourceItemRead,
    VerifyFindingRead,
)
from src.api.schemas.section import (
    ChapterNode,
    SectionBlockRewriteRequest,
    SectionCitation,
    SectionContentResponse,
    SectionContentUpdate,
    SectionNode,
    SectionRewriteRequest,
    SectionTreeResponse,
)
from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.state import ProjectState
from src.core.types import (
    ProjectStage,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    SourceRef,
    SourceType,
)
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.review_point import ReviewPoint
from src.db.models.section import Section
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.verify_finding import VerifyFinding
from src.prompts import list_presets, load_preset
from src.services.generation.planner import MAX_SECTIONS
from src.services.indexing.vector import SourceInput
from src.services.prompts import resolve_analysts
from src.workflows import cancel
from src.workflows.events import emit_error, last_step
from src.workflows.runner import get_pending_gate, is_running, queue_status, resume_run, start_run

# 단계 기반 근사 진행률 — 섹션 단위 세밀화는 추후 진행 이벤트로
_STAGE_PERCENT: dict[str, int] = {
    ProjectStage.CREATED.value: 0,
    ProjectStage.RESEARCHING.value: 20,
    ProjectStage.INDEXING.value: 40,
    ProjectStage.WRITING.value: 60,
    ProjectStage.REVIEWING.value: 85,
    ProjectStage.COMPLETED.value: 100,
    ProjectStage.ARCHIVED.value: 100,
    ProjectStage.CANCELLED.value: 0,
}

# 목록 화면의 '진행 중' 탭 — 단일 단계가 아니라 완료·보관·취소가 아닌 모든 진행 단계를 묶는다.
# (created·researching·indexing·writing·reviewing) 프론트는 status=in_progress로 요청한다.
_IN_PROGRESS_FILTER = "in_progress"
_IN_PROGRESS_STATUSES = (
    ProjectStage.CREATED.value,
    ProjectStage.RESEARCHING.value,
    ProjectStage.INDEXING.value,
    ProjectStage.WRITING.value,
    ProjectStage.REVIEWING.value,
)

logger = structlog.get_logger(__name__)

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
    # 목차는 사람이 만든다(2026-08-03 확정): AI 목차 설계 경로를 쓰지 않으므로
    # outline 없는 생성은 거부한다 — 실행 시점이 아니라 생성 시점에 막는다.
    if data.config.get("outline") is None:
        raise ValidationError(
            message="목차가 필요합니다 - 생성 화면에서 장·절을 구성하세요 (config.outline)",
            code="OUTLINE_REQUIRED",
        )
    _validate_outline_config(data.config, await _known_analyst_names(session, current_user.id))
    # 한도 사전 검사 — 초과 상태면 생성 자체를 429로 막는다(만들어놓고 실행 못 하는 orphan·
    # '생성됨'+'실행 실패' 겹침 방지). 메시지는 사람이 읽을 수 있는 사유를 그대로 전달한다.
    from src.clients.llm.quota_gate import check_user_quota

    await check_user_quota(current_user.id)
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
    # 커밋 소유권 규칙의 예외: 생성 직후 클라이언트가 곧바로 /run·조회를 부른다
    # (자동 실행 UX). 티어다운 커밋은 응답 전송 뒤라 그 사이 도착한 요청이
    # 미커밋 행을 못 보고 404가 난다(2026-08-03 실측, 13ms 간격) — 응답 전에
    # 커밋한다. expire_on_commit=False라 이후 직렬화도 안전.
    await session.commit()
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
    if status is not None and status != _IN_PROGRESS_FILTER and status not in _STAGE_PERCENT:
        raise ValidationError(
            message=(
                f"알 수 없는 status: {status} "
                f"(가능: {_IN_PROGRESS_FILTER}, {', '.join(_STAGE_PERCENT)})"
            ),
            code="INVALID_STATUS_FILTER",
        )
    if scope not in ("mine", "all"):
        raise ValidationError(message="scope는 mine 또는 all 이어야 합니다", code="INVALID_SCOPE")
    is_admin = current_user.role in ("super_admin", "admin")

    stmt = select(Project).options(selectinload(Project.owner))
    # 가시성: 일반 사용자는 항상 자기 것. 관리자는 scope=all일 때만 전체.
    if not (is_admin and scope == "all"):
        stmt = stmt.where(Project.owner_id == current_user.id)
    if status == _IN_PROGRESS_FILTER:
        stmt = stmt.where(Project.status.in_(_IN_PROGRESS_STATUSES))
    elif status is not None:
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
    # 한도 사전 검사 — 시작됐다가 첫 LLM 콜에서 조용히 죽는 대신 여기서 429로 알린다.
    from src.clients.llm.quota_gate import check_user_quota

    await check_user_quota(project.owner_id)
    if not await start_run(project.id):
        raise ValidationError(
            message="이미 실행 중인 프로젝트입니다",
            code="ALREADY_RUNNING",
        )
    return RunResponse(project_id=str(project.id), status=ProjectStage.RESEARCHING)


_TERMINAL_STATUSES = (
    ProjectStage.COMPLETED.value,
    ProjectStage.ARCHIVED.value,
    ProjectStage.CANCELLED.value,
)


@router.post("/{project_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, str]:
    """진행 중인 실행을 취소한다(협조적 — 단계·절 경계에서 멈춘다).

    - 실행 중: 취소 신호만 보내고 즉시 반환(status="cancelling"). 러너가 다음 경계에서
      관측해 CANCELLED로 마무리하고 WS로 알린다.
    - 게이트 대기 등 비실행 상태: 즉시 CANCELLED로 확정하고 대기 게이트를 해소한다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if project.status in _TERMINAL_STATUSES:
        raise ValidationError(
            message=f"이미 종료된 프로젝트입니다(현재: {project.status})",
            code="PROJECT_NOT_CANCELLABLE",
        )
    if project.status == ProjectStage.CREATED.value:
        raise ValidationError(
            message="아직 시작하지 않은 프로젝트입니다", code="PROJECT_NOT_RUNNING"
        )

    if is_running(project.id):
        # 실행 중 — 협조적 취소 신호. 러너가 CANCELLED 확정 + WS 통지.
        cancel.request(project.id)
        return {"project_id": str(project.id), "status": "cancelling"}

    # 비실행(게이트 대기 등) — 즉시 확정. 대기 게이트는 취소로 해소.
    project.status = ProjectStage.CANCELLED.value
    await session.execute(
        update(ReviewPoint)
        .where(ReviewPoint.project_id == project.id, ReviewPoint.status == "pending")
        .values(status="resolved", resolved_at=clock_now(), decision={"outcome": "cancelled"})
    )
    await session.flush()
    emit_error(project.id, "cancelled", "실행을 취소했습니다")
    return {"project_id": str(project.id), "status": "cancelled"}


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
                # 실행 시작·마지막 활동 근사 — 첫/마지막 LLM 콜 시각 (경과 시간 표시용)
                func.min(TokenUsage.created_at),
                func.max(TokenUsage.created_at),
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
        # 첫 LLM 콜이 끝나기 전(token_usage 0행)엔 상태 전이 시각(updated_at)으로 폴백 —
        # created→researching 직후 구간에서도 경과 시간이 새로고침에 초기화되지 않게.
        started_at=usage[2] or (project.updated_at if project.status != "created" else None),
        last_activity_at=usage[3],
        queue_position=(queue_status(project.id) or {}).get("position"),
        active_step=_active_step_label(project.status, project.id),
    )


def _active_step_label(status: str, project_id: UUID) -> str | None:
    """실행 중 프로젝트의 최근 세부 단계 라벨 — 스테퍼 서브라벨용(인메모리, 단일 워커)."""
    if status not in ("researching", "indexing", "writing", "reviewing"):
        return None
    event = last_step(project_id)
    if event is None or event.get("status") != "started":
        return None
    return str(event.get("step") or "") or None


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
    """프로젝트 영구 삭제 — 파이프라인이 실제 실행 중인 순간만 막는다.

    (2026-08-03 완화: 이전엔 완료·보관만 허용했으나, 게이트 대기·실패 잔류
    프로젝트를 지울 수 없어 실험 잔재가 쌓였다.) 하위 데이터(sources·chunks·
    raptor·consistency·review_points·sections)는 DB FK CASCADE로 함께 삭제되고,
    token_usage는 SET NULL이라 사용량·비용 기록은 남는다. 디스크의 완성본
    (<export_dir>/<id>.hwpx)도 함께 지운다 — 같은 id로 재조립될 일이 없어
    남겨두면 라이브러리 '완성본' 뷰에 유령 파일로 남는다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if is_running(project.id):
        raise ValidationError(
            message="실행 중인 프로젝트는 삭제할 수 없습니다 - 게이트 도달 후 다시 시도하세요",
            code="PROJECT_RUNNING",
        )
    # ORM 관계는 lazy="raise"라 session.delete()의 관계 로딩을 피하고
    # DB FK CASCADE에 맡기는 Core DELETE를 쓴다.
    await session.execute(delete(Project).where(Project.id == project.id))
    export_path = Path(settings.export_dir) / f"{project.id}.hwpx"
    try:
        export_path.unlink(missing_ok=True)
    except OSError:
        # 파일 잠금 등으로 못 지워도 삭제 자체는 성공 처리(다음 삭제/정리 때 재시도)
        logger.warning("project.export_cleanup_failed", project_id=str(project.id))


MAX_SOURCE_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB — 직접 업로드 자료 상한
_FILE_SOURCE_LABEL = {"upload": "업로드 파일", "library": "라이브러리 자료"}


def _to_source_item(row: ProjectSource) -> SourceItemRead:
    """project_sources 행 → 자료 검토 페이지용 항목.

    web_search는 수집 원문(metadata.content_md) 발췌를 미리보기로, 파일 소스(upload·library)는
    색인된 조각 수를 근거(has_content)로 삼는다 — 파일은 파싱·청킹돼 chunks에만 저장되고
    metadata엔 원문을 두지 않는다.
    """
    from src.workflows.stages import _source_preview, has_usable_content, relevance_excerpt

    meta = row.metadata_ or {}
    if row.source_type in _FILE_SOURCE_LABEL:
        chunks = int(meta.get("chunks") or 0)
        label = _FILE_SOURCE_LABEL[row.source_type]
        preview = f"{label} · {chunks}개 조각으로 색인됨" if chunks else f"{label} · 색인 대기"
        return SourceItemRead(
            id=row.id,
            source_type=row.source_type,
            title=row.title,
            url=row.url,
            reliability=row.reliability,
            is_included=row.is_included,
            preview=preview,
            has_content=chunks > 0,
            created_at=row.created_at,
        )
    content_md = meta.get("content_md") or ""
    usable = has_usable_content(content_md)
    matched = list(meta.get("matched_sections") or [])
    return SourceItemRead(
        id=row.id,
        source_type=row.source_type,
        title=row.title,
        url=row.url,
        reliability=row.reliability,
        is_included=row.is_included,
        matched_sections=matched,
        page_age=meta.get("page_age"),
        # 관련 절 키워드 주변 발췌 우선(관련성 근거), 없으면 본문 앞부분
        preview=(relevance_excerpt(content_md, matched) or _source_preview(content_md))
        if usable
        else None,
        has_content=usable,
        created_at=row.created_at,
    )


@router.get("/{project_id}/sources", response_model=list[SourceItemRead])
async def list_project_sources(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[SourceItemRead]:
    """자료 검토 페이지용 자료 풀 — project_sources 행 + metadata 신호.

    수집(collect)이 스테이징한 전 출처 + 사용자가 추가한 파일 소스를 돌려준다
    (제외분 포함 — is_included로 구분).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    rows = (
        (
            await session.execute(
                select(ProjectSource)
                .where(ProjectSource.project_id == project.id)
                .order_by(ProjectSource.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_to_source_item(row) for row in rows]


@router.patch("/{project_id}/sources/{source_id}", response_model=SourceItemRead)
async def update_project_source(
    project_id: UUID,
    source_id: UUID,
    data: SourceIncludeUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceItemRead:
    """자료 채택/제외 토글 — 제외분은 색인(index)·검색 근거에서 빠진다."""
    project = await _get_authorized_project(project_id, session, current_user)
    row = (
        await session.execute(
            select(ProjectSource).where(
                ProjectSource.project_id == project.id, ProjectSource.id == source_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    row.is_included = data.is_included
    await session.flush()
    return _to_source_item(row)


@router.delete(
    "/{project_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project_source(
    project_id: UUID,
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """업로드·라이브러리 자료 삭제 — 행과 색인 청크(FK CASCADE)를 함께 제거한다.

    웹 수집 자료는 수집 이력이 검토 감사 근거라 삭제 대신 제외(is_included)로만
    다룬다. 본문 작성이 시작된 뒤에는 인용([n]→chunk)이 청크를 참조하므로 삭제를
    금지한다 — 색인 중(indexing)도 청크 생성과 경합하므로 잠근다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if project.status not in ("created", "researching"):
        raise ValidationError(
            message="본문 작성 단계 이후에는 자료를 삭제할 수 없습니다 - 제외로 전환해 주세요.",
            code="SOURCE_DELETE_LOCKED",
        )
    row = (
        await session.execute(
            select(ProjectSource).where(
                ProjectSource.id == source_id, ProjectSource.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    if row.source_type not in ("upload", "library"):
        raise ValidationError(
            message="웹 수집 자료는 삭제할 수 없습니다 - 제외로 처리해 주세요.",
            code="SOURCE_NOT_DELETABLE",
        )
    # 업로드 원본 파일 정리(라이브러리 원본은 라이브러리 소유 — 건드리지 않는다).
    # 파일 정리 실패는 삭제를 막지 않는다 — 행·청크 제거가 본질이고 파일은 흔적일 뿐.
    if row.source_type == "upload" and row.upload_path:
        try:
            Path(row.upload_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("source.upload_file_cleanup_failed", path=row.upload_path)
    await session.delete(row)
    await session.flush()


async def _index_file_source(
    session: AsyncSession,
    source: SourceInput,
    *,
    error_context: str,
) -> ProjectSource:
    """파일 소스 1건을 색인하고 project_sources 행을 반환한다.

    색인기(index_source)는 자체 세션에서 UPSERT+chunks를 커밋하므로, 커밋된 행을 이 요청
    세션에서 재조회해 metadata(원산지·조각 수)를 채운다. 지원하지 않는 형식이면 명확한
    한국어 오류로 변환한다.
    """
    from src.clients.parser import UnsupportedFormatError
    from src.services.indexing.vector import build_vector_indexing_service

    service = build_vector_indexing_service()
    try:
        result = await service.index_source(source)
    except UnsupportedFormatError as exc:
        raise ValidationError(
            message="지원하지 않는 파일 형식입니다(PDF·HWPX·DOCX·MD·TXT만 색인할 수 있습니다).",
            code="UNSUPPORTED_SOURCE_FORMAT",
        ) from exc
    except Exception as exc:  # 파싱·임베딩 실패 — 원인 코드 노출 없이 명확한 메시지
        logger.warning("source.index_failed", context=error_context, exc_info=True)
        raise ValidationError(
            message="자료를 색인하지 못했습니다. 파일이 손상되지 않았는지 확인해 주세요.",
            code="SOURCE_INDEX_FAILED",
        ) from exc
    row = (
        await session.execute(select(ProjectSource).where(ProjectSource.id == result.source_id))
    ).scalar_one()
    row.metadata_ = {
        **(row.metadata_ or {}),
        "origin": source.source_type,
        "chunks": result.chunks_created,
    }
    await session.flush()
    return row


@router.post(
    "/{project_id}/sources/upload",
    response_model=SourceItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_source(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    file: Annotated[UploadFile, File()],
) -> SourceItemRead:
    """직접 업로드 자료 — 파일 저장 후 즉시 색인(청킹·임베딩).

    채택(is_included) 기본 참. 나중에 제외하면 검색 시점에 자동으로 근거에서 빠진다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    content = await file.read()
    if not content:
        raise ValidationError(message="빈 파일입니다.", code="EMPTY_UPLOAD")
    if len(content) > MAX_SOURCE_UPLOAD_BYTES:
        raise ValidationError(
            message=f"파일이 너무 큽니다(최대 {MAX_SOURCE_UPLOAD_BYTES // (1024 * 1024)}MB).",
            code="UPLOAD_TOO_LARGE",
        )
    safe_name = Path(file.filename or "untitled").name
    dest_dir = Path(settings.library_dir) / "project_sources" / str(project.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid4()}_{safe_name}"
    dest.write_bytes(content)
    row = await _index_file_source(
        session,
        SourceInput(
            project_id=project.id,
            source_type="upload",
            file_path=dest,
            upload_path=str(dest),
            title=safe_name,
        ),
        error_context=f"upload:{project.id}:{safe_name}",
    )
    return _to_source_item(row)


@router.post(
    "/{project_id}/sources/library",
    response_model=SourceItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_library_source(
    project_id: UUID,
    data: LibraryAttachRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceItemRead:
    """라이브러리 파일을 프로젝트 자료로 불러오기 — 원본 파일을 색인해 근거로 편입.

    개인 자료는 소유자·관리자만, 회사 공유 자료는 누구나 불러올 수 있다. 같은 파일을
    다시 불러오면 재색인된다(부분 UNIQUE 키로 중복 행이 생기지 않음).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    node = await session.get(LibraryNode, data.library_node_id)
    if node is None or node.type != "file":
        raise NotFoundError(
            message="라이브러리 파일을 찾을 수 없습니다.", code="LIBRARY_FILE_NOT_FOUND"
        )
    is_admin = current_user.role in ("super_admin", "admin")
    if node.is_personal and node.created_by != current_user.id and not is_admin:
        raise AuthorizationError(message="이 자료에 접근할 수 없습니다.", code="FORBIDDEN")
    if not node.file_path or not Path(node.file_path).is_file():
        raise ValidationError(
            message="원본 파일이 없어 불러올 수 없습니다(수집 원문만 있는 자료일 수 있습니다).",
            code="LIBRARY_FILE_MISSING",
        )
    row = await _index_file_source(
        session,
        SourceInput(
            project_id=project.id,
            source_type="library",
            file_path=Path(node.file_path),
            library_node_id=node.id,
            title=node.name,
        ),
        error_context=f"library:{project.id}:{node.id}",
    )
    return _to_source_item(row)


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


def _state_for_export(project: Project, rows: list[Section]) -> ProjectState:
    """sections 테이블의 확정 본문 → export_report가 먹는 최소 상태.

    후보/선택 구조를 절당 1후보로 재구성한다 — 편집된 최신 본문이 곧 선택본.
    """
    plans: list[SectionPlan] = []
    sets: list[SectionCandidateSet] = []
    selections: dict[UUID, UUID] = {}
    for row in rows:
        plan = SectionPlan(
            section_id=row.id,
            chapter_number=row.chapter_number,
            section_number=row.section_number,
            title=row.title,
        )
        draft = SectionDraft(section_id=row.id, content=row.content or "", cited_chunk_ids=[])
        candidate = SectionCandidate(draft=draft)
        plans.append(plan)
        sets.append(SectionCandidateSet(section_id=row.id, candidates=[candidate]))
        selections[row.id] = candidate.candidate_id
    return ProjectState(
        project_id=project.id,
        user_id=project.owner_id,
        topic=project.topic,
        section_plan=plans,
        section_candidates=sets,
        section_selections=selections,
        options=project.config or {},
    )


@router.get("/{project_id}/export")
async def download_export(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FileResponse:
    """완성된 보고서 HWPX 다운로드 — 항상 최신 sections로 재렌더 후 서빙.

    조립 시점 파일만 서빙하면 미리보기·편집에서 고친 본문이 다운로드에 반영되지
    않는다(2026-08-04 지적). 렌더는 순수 코드(~1초)라 다운로드 시점 재렌더가
    가장 안전하다. 재렌더 실패 시 조립 시점 파일로 폴백, 그것도 없으면 404.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    path = Path(settings.export_dir) / f"{project.id}.hwpx"
    rows = await _load_sections(session, project.id)
    if rows:
        try:
            from src.services.export.report import export_report

            state = _state_for_export(project, rows)
            # 출처 최종장 — 채택 자료를 실어야 렌더된다(조립 경로와 동일 규칙).
            src_rows = (
                (
                    await session.execute(
                        select(ProjectSource)
                        .where(
                            ProjectSource.project_id == project.id,
                            ProjectSource.is_included.is_(True),
                        )
                        .order_by(ProjectSource.created_at)
                    )
                )
                .scalars()
                .all()
            )
            state = state.model_copy(
                update={
                    "sources": [
                        SourceRef(
                            id=r.id,
                            source_type=SourceType(r.source_type),
                            title=r.title or r.url or "(제목 없음)",
                            url=r.url,
                            reliability=r.reliability,
                        )
                        for r in src_rows
                    ]
                }
            )
            # 약어 설명은 조립 시 저장한 사전(projects.glossary)에서 — 재렌더는 순수 코드.
            path = export_report(state, glossary=project.glossary)
        except Exception:
            logger.warning("export.rerender_failed", project_id=str(project.id), exc_info=True)
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
    # 한도 사전 검사 — 재개 구간(색인·작성·추가 검색)도 LLM 비용이 크다.
    from src.clients.llm.quota_gate import check_user_quota

    await check_user_quota(project.owner_id)
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


# 검토 중(writing·reviewing) 편집도 허용한다 — 통합 검토 화면(2026-08-07)의 정식
# 경로. 편집 결과는 resume 시 overlay_working_copy가 payload 후보보다 우선 반영한다.


# 본문 인용 마커 — 작성기(_extract_cited_ids)와 같은 [N] 규약.
_CITE_NUM_RE = re.compile(r"\[(\d+)\]")


def _citation_numbers(content: str) -> list[int]:
    """본문의 [N] 인용 번호를 등장 순서대로 중복 없이 추출.

    작성 시점 cited_chunk_ids(→sections.source_ids)가 바로 이 순서로 저장되므로
    (candidates._extract_cited_ids), 위치 대응이 곧 [N]↔출처 매핑이다.
    """
    numbers: list[int] = []
    seen: set[int] = set()
    for m in _CITE_NUM_RE.finditer(content):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            numbers.append(n)
    return numbers


def _citations_from_numbers(
    numbers: list[int], sources_ordered: list[ProjectSource]
) -> list[SectionCitation]:
    """전역 번호 → 채택 자료 목록(수집 순서) 직해석.

    조립 시 renumber가 본문 번호를 이 순서로 재매핑하므로(출처 최종장과 동일),
    번호 n = sources_ordered[n-1]이다. 범위 밖 번호(수동 편집 잔재)는 불명 처리.
    """
    citations: list[SectionCitation] = []
    for n in sorted(numbers):
        if 1 <= n <= len(sources_ordered):
            r = sources_ordered[n - 1]
            citations.append(
                SectionCitation(
                    number=n,
                    title=r.title or r.url or "(제목 없음)",
                    url=r.url,
                    source_id=str(r.id),
                    reliability=r.reliability,
                )
            )
        else:
            citations.append(SectionCitation(number=n, title="(출처 불명 - 번호 범위 밖)"))
    return citations


async def _section_citations(session: AsyncSession, row: Section) -> list[SectionCitation]:
    """본문의 전역 인용 번호 [n]을 출처(제목·URL·신뢰도) 목록으로 푼다.

    번호 체계 = 채택 자료의 수집 순서(renumber·출처 최종장과 동일 단일 진실).
    """
    numbers = _citation_numbers(row.content or "")
    if not numbers:
        return []
    sources_ordered = (
        (
            await session.execute(
                select(ProjectSource)
                .where(
                    ProjectSource.project_id == row.project_id,
                    ProjectSource.is_included.is_(True),
                )
                .order_by(ProjectSource.created_at)
            )
        )
        .scalars()
        .all()
    )
    return _citations_from_numbers(numbers, list(sources_ordered))


def _section_content(
    row: Section, citations: list[SectionCitation] | None = None
) -> SectionContentResponse:
    return SectionContentResponse(
        id=str(row.id),
        title=row.title,
        content=row.content,
        source_ids=[str(s) for s in row.source_ids],
        qa_status=row.qa_status,
        level=row.level,
        citations=citations or [],
    )


@router.get("/{project_id}/sections/{section_id}", response_model=SectionContentResponse)
async def get_section_content(
    project_id: UUID,
    section_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionContentResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_section(session, project.id, section_id)
    return _section_content(row, await _section_citations(session, row))


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
    # 본문 편집도 '최근 수정'에 반영 — 다운로드 재렌더(HWPX)와 함께 편집의
    # 흔적이 프로젝트 레벨에서 보이게 한다.
    project.updated_at = clock_now()
    await session.flush()
    await session.refresh(row)
    return _section_content(row, await _section_citations(session, row))


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
    content = draft.content
    try:
        # 재작성 결과도 전역 번호로 재매핑 — 문서의 나머지 절·출처장과 번호 체계 유지.
        from src.services.sections.renumber import build_chunk_to_global, renumber_content

        mapping = await build_chunk_to_global(project.id, set(draft.cited_chunk_ids))
        content = renumber_content(draft.content, list(draft.cited_chunk_ids), mapping)
    except Exception:
        logger.warning("rewrite.renumber_failed", project_id=str(project.id), exc_info=True)
    row.content = content
    row.source_ids = list(draft.cited_chunk_ids)
    row.status = "completed"
    row.qa_status = "passed"
    project.updated_at = clock_now()
    await session.flush()
    await session.refresh(row)
    return _section_content(row, await _section_citations(session, row))


async def _default_block_rewriter(project: Project, row: Section, data) -> str:
    """블록 국소 재작성 — 검색 없이 절 문맥+기존 인용 범위 안에서 LLM 1콜."""
    from src.services.sections.edit import rewrite_block

    return await rewrite_block(
        section_title=row.title,
        content=row.content,
        block=data.block,
        instruction=data.instruction,
        user_id=project.owner_id,
        project_id=project.id,
    )


# 주입 지점 — 테스트는 이 전역을 fake로 교체한다(실LLM 회피).
_block_rewriter = _default_block_rewriter


@router.post(
    "/{project_id}/sections/{section_id}/rewrite-block", response_model=SectionContentResponse
)
async def rewrite_section_block(
    project_id: UUID,
    section_id: UUID,
    data: SectionBlockRewriteRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionContentResponse:
    """블록 재작성 — 본문에서 지정 블록 하나만 지시에 따라 고쳐 치환 저장한다.

    통합 검토 화면의 블록 단위 편집용. 전체 재작성(rewrite)과 달리 검색을 다시
    돌리지 않고 기존 인용 범위 안에서만 고친다 — 인용 번호·출처 목록이 유지된다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_section(session, project.id, section_id)
    if data.block not in row.content:
        raise ValidationError(
            message="지정한 블록을 본문에서 찾을 수 없습니다(본문이 갱신됐을 수 있음)",
            code="BLOCK_NOT_FOUND",
        )
    new_block = await _block_rewriter(project, row, data)
    if not new_block.strip():
        raise ValidationError(message="재작성 결과가 비어 있습니다", code="BLOCK_REWRITE_EMPTY")
    # 첫 번째 일치만 치환 — 프론트가 보낸 블록은 화면의 특정 위치지만 동일 문단이
    # 중복될 수 있어, 원문 전체가 아니라 한 곳만 바꾼다.
    row.content = row.content.replace(data.block, new_block, 1)
    project.updated_at = clock_now()
    await session.flush()
    await session.refresh(row)
    return _section_content(row, await _section_citations(session, row))
