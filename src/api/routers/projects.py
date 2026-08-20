import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4
from zipfile import BadZipFile

import structlog
from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.cost_limit import enforce_cost_limit
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import require_writer
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
    PresetVisibilityUpdate,
    ProjectCreate,
    ProjectRead,
    ReportVersionDetail,
    ReportVersionRead,
    SourceIncludeUpdate,
    SourceItemRead,
    UserPresetRead,
    UserPresetUpsert,
    VerifyFindingRead,
    VerifyFindingResolve,
    VersionDiffEntry,
    VersionDiffResponse,
    VersionSection,
)
from src.api.schemas.section import (
    ChapterNode,
    ClaimAlignmentRead,
    EvidenceChunk,
    EvidenceInfo,
    GroundedNumberRead,
    SectionBlockRewriteRequest,
    SectionCitation,
    SectionContentResponse,
    SectionContentUpdate,
    SectionEvidenceResponse,
    SectionNode,
    SectionRewriteRequest,
    SectionTreeResponse,
    SourceChunkRead,
    SourceDocumentResponse,
)
from src.api.schemas.source_stats import SourceUsageResponse
from src.api.uploads import read_validated_upload
from src.core.charts import has_chart_fence
from src.core.citations import numbers_in_order
from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.outline import normalize_outline
from src.core.section_plan import (
    SECTION_PLAN_KEY,
    dump_section_plan,
    load_section_plan,
    plan_from_config,
)
from src.core.state import ProjectState
from src.core.types import (
    ProjectStage,
    ReviewGate,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    SourceRef,
    SourceType,
)
from src.db.models.chunk import Chunk
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.review_point import ReviewPoint
from src.db.models.section import Section
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_prompt import UserPrompt
from src.db.models.verify_finding import VerifyFinding
from src.db.session import async_session_maker
from src.prompts import list_presets, load_preset
from src.services.export.report import export_file_pattern, export_filename
from src.services.generation.planner import MAX_SECTIONS
from src.services.indexing.exclusion import apply_index_outcome
from src.services.indexing.published_year import year_from_page_age
from src.services.indexing.vector import SourceInput
from src.services.prompts import resolve_analysts
from src.services.qa.alignment import align_section
from src.services.qa.gate import uncited_units
from src.services.sections.evidence import marker_chunk_ids
from src.services.stats.source_usage import build_source_usage
from src.services.user_presets import (
    create_user_preset,
    delete_user_preset,
    get_readable_preset,
    get_user_preset,
    import_public_preset,
    list_public_presets,
    list_user_presets,
    parse_personal_key,
    personal_preset_key,
    update_user_preset,
)
from src.workflows import cancel
from src.workflows.events import active_steps, emit_error, last_event_at, last_step
from src.workflows.runner import get_pending_gate, is_running, queue_status, resume_run, start_run

# 단계 기반 근사 진행률 — 섹션 단위 세밀화는 추후 진행 이벤트로
_STAGE_PERCENT: dict[str, int] = {
    ProjectStage.CREATED.value: 0,
    ProjectStage.PLANNING.value: 5,
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
    ProjectStage.PLANNING.value,
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


def _validate_outline_config(
    config: dict, known_analysts: set[str], *, fresh_ids: bool = False
) -> dict:
    """config.outline이 있으면 형태·섹션 수·에이전트 이름을 검증하고 **정규화해 돌려준다**.

    outline은 planner LLM을 우회해 그대로 실행되므로 생성/수정 시점에 막는 게
    마지막 방어선이다. known_analysts는 개인→시스템 병합 카탈로그의 id·name 집합
    (개인 에이전트도 배정 가능하도록 호출부에서 resolve_analysts로 계산해 넘긴다).

    정규화(core/outline.normalize_outline)는 두 가지다: ① 장·절에 안정 id를 채운다
    (절 정체성의 닻 — plan·리허설·본문 행이 이 id를 따른다), ② builds_on의 번호
    표기("4.1")를 제출된 위치 기준으로 id 토큰("s:<uuid>")으로 바꿔 저장한다 —
    이후 절을 끼워 넣어도 참조가 말없이 다른 절을 가리키지 않는다. 호출부는
    **반드시 반환값을 저장**해야 한다(원본 config는 id가 비어 있다).
    """
    outline = config.get("outline")
    if outline is None:
        return config
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
    # builds_on 검증+정규화 — 유령 절·자기 참조·상한 초과는 생성 시점에 막는다.
    # 실행 시점(assign_levels)에도 같은 가드가 있지만 그건 절단·경고이고, 여기는
    # 사람이 고칠 수 있는 마지막 자리라 명시적으로 알린다.
    normalized_outline, ref_errors = normalize_outline(outline, fresh_ids=fresh_ids)
    if ref_errors:
        raise ValidationError(
            message=f"builds_on 오류: {ref_errors[0]}",
            code="INVALID_BUILDS_ON",
        )
    return {**config, "outline": normalized_outline}


async def _validate_rules_config(session: AsyncSession, owner_id: UUID, config: dict) -> None:
    """config.rules(개인 작성 규칙 id 목록)가 내 규칙인지 확인한다.

    규칙은 보고서 전체의 문체·출처 계약이라 프로젝트에서 한 번 고르고 고정된다.
    남의 id를 넣어도 조용히 무시되면 '골랐는데 안 먹는' 거짓 스위치가 되므로 막는다.
    """
    raw = config.get("rules")
    if not raw:
        return
    if not isinstance(raw, list) or len(raw) > 10:
        raise ValidationError(message="rules 형식이 올바르지 않습니다", code="INVALID_RULES")
    try:
        ids = [UUID(str(x)) for x in raw]
    except (ValueError, TypeError):
        raise ValidationError(
            message="rules 형식이 올바르지 않습니다", code="INVALID_RULES"
        ) from None
    rows = (
        (
            await session.execute(
                select(UserPrompt.id).where(
                    UserPrompt.owner_id == owner_id,
                    UserPrompt.kind == "rule",
                    UserPrompt.id.in_(ids),
                )
            )
        )
        .scalars()
        .all()
    )
    missing = sorted(str(i) for i in set(ids) - set(rows))
    if missing:
        raise ValidationError(
            message=f"내 작성 규칙이 아닙니다: {', '.join(missing)}", code="UNKNOWN_RULE"
        )


async def _known_analyst_names(session: AsyncSession, owner_id: UUID) -> set[str]:
    """개인→시스템 병합 에이전트의 id·name 집합(outline 검증용)."""
    known: set[str] = set()
    for a in await resolve_analysts(session, owner_id):
        known.add(a.id)
        known.add(a.name)
    return known


def _preset_counts(outline: dict) -> tuple[int, int]:
    chapters = outline.get("chapters") or []
    return len(chapters), sum(len(ch.get("sections") or []) for ch in chapters)


@presets_router.get("", response_model=list[PresetRead])
async def get_presets(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[PresetRead]:
    """보고서 유형 프리셋 카탈로그 — 시스템(파일) + 내 프리셋(DB) 병합.

    자유 주제는 preset=None으로 생성하면 된다. 개인 프리셋은 scope='personal',
    id="u:<uuid>"로 실려 시스템 프리셋과 같은 선택 코드 경로를 탄다.
    """
    items = [
        PresetRead(
            id=p.id,
            name=p.name,
            desc=p.desc,
            n_chapters=len(p.chapters),
            n_sections=sum(len(ch.sections) for ch in p.chapters),
        )
        for p in list_presets()
    ]
    for row in await list_user_presets(session, current_user.id):
        n_ch, n_sec = _preset_counts(row.outline)
        items.append(
            PresetRead(
                id=personal_preset_key(row.id),
                name=row.name,
                desc=row.description or "내가 저장한 목차 구성",
                n_chapters=n_ch,
                n_sections=n_sec,
                scope="personal",
                updated_at=row.updated_at,
                is_public=row.is_public,
            )
        )
    # 남이 공개한 프리셋 — 덮어쓰지 않고 뒤에 붙기만 한다(에이전트 공유와 같은 규약).
    taken = {p.name for p in items}
    for row, owner_name in await list_public_presets(session, current_user.id):
        n_ch, n_sec = _preset_counts(row.outline)
        # 이름이 겹칠 때만 소유자를 덧붙인다 — 안 겹치면 원래 이름 그대로 보인다.
        name = row.name if row.name not in taken else f"{row.name} ({owner_name})"
        taken.add(name)
        items.append(
            PresetRead(
                id=personal_preset_key(row.id),
                name=name,
                desc=row.description or f"{owner_name}이 공개한 목차 구성",
                n_chapters=n_ch,
                n_sections=n_sec,
                scope="shared",
                owner_name=owner_name,
                updated_at=row.updated_at,
            )
        )
    return items


@presets_router.post(
    "/personal", response_model=UserPresetRead, status_code=status.HTTP_201_CREATED
)
async def create_personal_preset(
    data: UserPresetUpsert,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UserPresetRead:
    """목차 편집기의 현재 구성을 내 프리셋으로 저장 — 프로젝트 간 구성 재사용."""
    row = await create_user_preset(
        session,
        current_user.id,
        name=data.name.strip(),
        description=data.description,
        outline={"chapters": [ch.model_dump() for ch in data.chapters]},
        is_public=data.is_public,
    )
    n_ch, n_sec = _preset_counts(row.outline)
    return UserPresetRead(
        id=row.id,
        key=personal_preset_key(row.id),
        name=row.name,
        description=row.description,
        n_chapters=n_ch,
        n_sections=n_sec,
        is_public=row.is_public,
        updated_at=row.updated_at,
    )


@presets_router.put("/personal/{preset_id}", response_model=UserPresetRead)
async def update_personal_preset(
    preset_id: UUID,
    data: UserPresetUpsert,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UserPresetRead:
    row = await update_user_preset(
        session,
        current_user.id,
        preset_id,
        name=data.name.strip(),
        description=data.description,
        outline={"chapters": [ch.model_dump() for ch in data.chapters]},
        is_public=data.is_public,
    )
    n_ch, n_sec = _preset_counts(row.outline)
    return UserPresetRead(
        id=row.id,
        key=personal_preset_key(row.id),
        name=row.name,
        description=row.description,
        n_chapters=n_ch,
        n_sections=n_sec,
        is_public=row.is_public,
        updated_at=row.updated_at,
    )


@presets_router.patch("/personal/{preset_id}/visibility", response_model=UserPresetRead)
async def set_preset_visibility(
    preset_id: UUID,
    data: PresetVisibilityUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UserPresetRead:
    """공개 토글 전용 — 목록에서 바로 켜고 끈다(2026-08-20 사용자 요청).

    PUT은 목차 전체를 요구해 토글용으로 못 쓴다(목록 화면은 outline을 안 들고 있다).
    """
    row = await get_user_preset(session, current_user.id, preset_id)
    row.is_public = data.is_public
    await session.flush()
    await session.refresh(row)
    n_ch, n_sec = _preset_counts(row.outline)
    return UserPresetRead(
        id=row.id,
        key=personal_preset_key(row.id),
        name=row.name,
        description=row.description,
        n_chapters=n_ch,
        n_sections=n_sec,
        is_public=row.is_public,
        updated_at=row.updated_at,
    )


@presets_router.post(
    "/personal/import/{source_id}",
    response_model=UserPresetRead,
    status_code=status.HTTP_201_CREATED,
)
async def import_shared_preset(
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UserPresetRead:
    """공개된 남의 목차 프리셋을 내 것으로 가져온다(복제, 비공개로).

    바로 쓰는 건 목록에서 고르면 되고(3층 병합), 가져오기는 고쳐 쓸 때를 위한 것이다.
    """
    row = await import_public_preset(session, current_user.id, source_id)
    n_ch, n_sec = _preset_counts(row.outline)
    return UserPresetRead(
        id=row.id,
        key=personal_preset_key(row.id),
        name=row.name,
        description=row.description,
        n_chapters=n_ch,
        n_sections=n_sec,
        is_public=row.is_public,
        updated_at=row.updated_at,
    )


@presets_router.delete("/personal/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personal_preset(
    preset_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    await delete_user_preset(session, current_user.id, preset_id)


@presets_router.get("/{preset_key}", response_model=PresetDetailRead)
async def get_preset_detail(
    preset_key: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PresetDetailRead:
    """프리셋 전체 골격(챕터·섹션·방향·핵심포인트·담당 에이전트).

    생성 화면에서 프리셋을 클릭해 들어가면 이 골격이 목차 편집기의 초기값이 된다.
    "u:<uuid>"는 내 프리셋 — 시스템과 같은 응답 모양이라 프론트 로드 코드가 같다.
    """
    personal_id = parse_personal_key(preset_key)
    if personal_id is not None:
        # 내 것이거나 공개된 것 — 골격을 봐야 목차 편집기의 초기값으로 쓸 수 있다.
        # 관리자는 비공개도 열람만 가능(라이브러리 '사용자별 자료' 미러).
        row = await get_readable_preset(
            session,
            current_user.id,
            personal_id,
            is_admin=current_user.role in ("admin", "super_admin"),
        )
        return PresetDetailRead(
            id=preset_key,
            name=row.name,
            desc=row.description or "",
            domain_context="",
            chapters=row.outline.get("chapters") or [],
        )
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
                        builds_on=list(s.builds_on),
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

    시스템 → 내 개인(덮어쓰기/추가) → 남이 공개한 에이전트(추가) 순 병합 목록.
    """
    return [
        AnalystRead(
            id=a.id,
            name=a.name,
            cat=a.cat,
            desc=a.desc,
            pages=a.volume_target.pages if a.volume_target else None,
            shared=a.shared,
            owner_name=a.owner_name,
            queries=list(a.queries),
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
    current_user: Annotated[User, Depends(require_writer)],
) -> Project:
    # preset은 파일 카탈로그(src.prompts)가 단일 진실 — 생성 시점에 키를 검증해 확정한다.
    # None이면 자유 주제(프리셋 없는 일반 목차 설계), "u:<uuid>"면 내가 저장한 프리셋.
    if data.preset is not None:
        personal_id = parse_personal_key(data.preset)
        if personal_id is not None:
            try:
                # 남이 공개한 프리셋으로도 프로젝트를 만들 수 있어야 공유가 성립한다.
                await get_readable_preset(session, current_user.id, personal_id)
            except NotFoundError:
                raise ValidationError(
                    message=f"알 수 없는 프리셋입니다: {data.preset}", code="UNKNOWN_PRESET"
                ) from None
        else:
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
    # 서버 내부 키 제거 — 다른 프로젝트 config를 복사해 생성하는 경로(검증런 복제
    # 스크립트)가 _section_plan까지 실어 보내면, 새 프로젝트가 **남의 절 id로** 돈다.
    # sections.id는 전역 PK라 원본 프로젝트의 행이 살아 있는 동안 이 프로젝트의 절
    # 저장이 전부 duplicate key로 실패한다(2026-08-21 6차 검증런 실사고 — 증분 20회
    # + 조립 전량 저장까지 침묵 실패, 본문 유실). 계획·스냅샷은 서버가 만든다.
    stripped_config = {k: v for k, v in data.config.items() if k not in _INTERNAL_CONFIG_KEYS}
    # fresh_ids: sections.id는 전역 PK라, 남의 config를 복사해 만들어도 절 id가
    # 겹치면 안 된다 — 생성은 항상 새 정체성으로 시작한다.
    normalized_config = _validate_outline_config(
        stripped_config, await _known_analyst_names(session, current_user.id), fresh_ids=True
    )
    await _validate_rules_config(session, current_user.id, normalized_config)
    # 한도 사전 검사 — 초과 상태면 생성 자체를 429로 막는다(만들어놓고 실행 못 하는 orphan·
    # '생성됨'+'실행 실패' 겹침 방지). 메시지는 사람이 읽을 수 있는 사유를 그대로 전달한다.
    from src.clients.llm.quota_gate import check_user_quota

    await check_user_quota(current_user.id)
    project = Project(
        title=data.title,
        topic=data.topic,
        preset=data.preset,
        config=normalized_config,
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
    preset: str | None = None,
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
    # 보고서 유형(프리셋) 필터 — 목록에서 유형별로 좁혀 보기 위함. 자유 주제는
    # preset이 비어 있으므로 'blank' 토큰으로 그것만 고를 수 있게 한다.
    if preset:
        stmt = stmt.where(
            Project.preset.is_(None) if preset == "blank" else Project.preset == preset
        )
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
) -> ProjectRead:
    project = await _get_authorized_project(project_id, session, current_user)
    # 본문 총 글자 수 — DB에서 합산한다(수천 자 본문을 파이썬으로 끌어오지 않는다).
    total = (
        await session.execute(
            select(func.coalesce(func.sum(func.length(Section.content)), 0)).where(
                Section.project_id == project.id
            )
        )
    ).scalar_one()
    read = ProjectRead.model_validate(project)
    read.total_chars = int(total)
    return read


@router.post("/{project_id}/run", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    # 비용 한도 게이트를 실행 진입점에 배선 — 초과 시 프로젝트 조회 전에 429로 빠르게 막는다.
    # (enforce_cost_limit이 내부에서 get_current_active_user를 호출하고 User를 반환)
    current_user: Annotated[User, Depends(enforce_cost_limit)],
) -> RunResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    # 게이트 대기는 '멈춘 런'이 아니다 — 아래 크래시 복구용 재개와 겉모습(작업 단계
    # status + 살아 있는 태스크 없음)이 같아 /run이 승인 없이 다음 단계로 넘어갔다.
    # SOURCE_POOL을 건너뛰면 자료 채택·제외가 미반영인 채 작성되고, section_plan도
    # 미복원(pending payload는 _rehydrate_section_plan이 못 읽음) →
    # write.plan_fallback으로 목차가 2절(개요·분석)로 붕괴한다. 올바른 경로는 /decide.
    if await get_pending_gate(session, project.id) is not None:
        raise ValidationError(
            message="검토 대기 중입니다 - 검토를 완료(승인)해야 이어서 진행됩니다",
            code="GATE_PENDING",
        )
    # 새로 생성된 프로젝트만 실행(thread_id=project_id 재사용 충돌 회피).
    # 주의: 여기서 status를 미리 researching으로 바꾸면 안 된다 — 상태는 척추의
    # 진행 위치라서 선점하면 _execute가 research 단계를 건너뛴다(2026-08-03 실사고:
    # 자료 0건·게이트 없이 폴백 목차로 완료). 중복 기동 차단은 runner의
    # 인프로세스 가드(_RUNNING, 단일 워커 전제)가 담당한다.
    # 시작 전(created·cancelled)이거나, 작업 단계인데 살아 있는 실행이 없는 '멈춘' 런은
    # 이어서 재개한다. 후자는 프로세스 재시작·자원 고갈로 태스크가 사라진 경우로, 이
    # 경로가 없으면 보고서를 처음부터 다시 만드는 수밖에 없다(2026-08-09 실사고: 메모리
    # 고갈로 작성 3절에서 정지 → 재개 수단 없음). 재개 위치는 runner가 status로 판단한다.
    from src.workflows.runner import is_running

    resumable = project.status not in _UNRUNNABLE_STATUSES and not is_running(project.id)
    if project.status != ProjectStage.CREATED.value and not resumable:
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

# 다시 돌릴 수 없는 상태 — 취소는 여기 없다. 취소한 런은 직전 단계(config.cancelled_from)
# 부터 이어서 재개한다. 개요 화면이 취소된 프로젝트에 '다시 시작' 버튼을 띄우는데
# 가드가 막아 422가 나던 문제(2026-08-10).
_UNRUNNABLE_STATUSES = (
    ProjectStage.COMPLETED.value,
    ProjectStage.ARCHIVED.value,
)


@router.post("/{project_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
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
    # 재개 지점을 남겨야 '다시 시작'이 처음부터가 아니라 이어서 돈다.
    project.config = {**(project.config or {}), "cancelled_from": project.status}
    project.status = ProjectStage.CANCELLED.value
    await session.execute(
        update(ReviewPoint)
        .where(ReviewPoint.project_id == project.id, ReviewPoint.status == "pending")
        .values(status="resolved", resolved_at=clock_now(), decision={"outcome": "cancelled"})
    )
    await session.flush()
    emit_error(project.id, "cancelled", "실행을 취소했습니다")
    return {"project_id": str(project.id), "status": "cancelled"}


# 순수 생성 시간 계산의 '멈춤' 판정 간격. 이보다 긴 공백은 사람 검토 대기·중단·크래시로
# 보고 합계에서 뺀다(2026-08-09: 게이트 대기 9시간이 경과 시간에 그대로 들어가 22시간으로
# 표시돼 실제 생성 시간을 알 수 없었다).
_IDLE_GAP_SECONDS = 600


async def _active_seconds(session: AsyncSession, project_id: UUID) -> int:
    """LLM 호출 타임스탬프에서 '실제로 돌던 시간'만 합산한다.

    연속 호출 간격이 _IDLE_GAP_SECONDS 이하면 그 사이는 작업 중으로 보고 더한다.
    게이트 대기·정지 구간은 간격이 크므로 자연히 제외된다.
    """
    rows = (
        (
            await session.execute(
                select(TokenUsage.created_at)
                .where(TokenUsage.project_id == project_id)
                .order_by(TokenUsage.created_at)
            )
        )
        .scalars()
        .all()
    )
    total = 0.0
    for prev, cur in zip(rows, rows[1:], strict=False):
        gap = (cur - prev).total_seconds()
        if 0 <= gap <= _IDLE_GAP_SECONDS:
            total += gap
    return int(total)


class DesignBriefRead(BaseModel):
    """설계 브리프 기록 — 게이트가 닫힌 뒤에도 '무엇을 승인했는가'를 볼 수 있어야 한다.

    payload=AI 원안(게이트가 보여준 것), decision=사람의 결정(수정본 포함). 커밋된
    작성 계약은 decision의 ai_plan이 payload보다 우선한다(runner._commit_design_plan).
    """

    status: str  # pending | resolved
    payload: dict[str, Any]
    decision: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None


@router.get("/{project_id}/design-brief", response_model=DesignBriefRead)
async def get_design_brief(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DesignBriefRead:
    """최신 설계 브리프(대기 중이든 확정이든) — 확정 후 사후 열람용.

    승인하고 나면 게이트 payload는 progress에서 사라지는데, 브리프 화면이 그때
    빈 화면이 되면 '내가 무엇을 승인했는지'를 다시 볼 길이 없다(2026-08-15 지적).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = (
        await session.execute(
            select(ReviewPoint)
            .where(
                ReviewPoint.project_id == project.id,
                ReviewPoint.gate == ReviewGate.DESIGN_BRIEF.value,
            )
            .order_by(ReviewPoint.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(message="설계 브리프가 아직 없습니다", code="DESIGN_BRIEF_NOT_FOUND")
    return DesignBriefRead(
        status=row.status,
        payload=row.payload or {},
        decision=row.decision,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


@router.get("/{project_id}/progress", response_model=ProgressResponse)
async def get_progress(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ProgressResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    active_seconds = await _active_seconds(session, project.id)
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
    percent = _STAGE_PERCENT.get(project.status, 0)
    if project.status == ProjectStage.WRITING.value:
        # 작성이 벽시계의 몸통(~35분)인데 단계 고정값이면 내내 60%에 멈춰 보인다
        # (2026-08-20 지적). 완성 절 수로 60→85 구간을 보간한다 — 분모는 확정 목차,
        # 분자는 본문이 실제로 채워진 절(draft_store가 절 완료 즉시 영속).
        outline = (project.config or {}).get("outline") or {}
        total = sum(len(ch.get("sections") or []) for ch in outline.get("chapters") or [])
        if total > 0:
            done = (
                await session.execute(
                    select(func.count())
                    .select_from(Section)
                    .where(Section.project_id == project.id, func.length(Section.content) > 100)
                )
            ).scalar_one()
            percent = 60 + round(min(int(done), total) / total * 25)
    return ProgressResponse(
        project_id=str(project.id),
        status=ProjectStage(project.status),
        pending_gate=await get_pending_gate(session, project.id),
        percent=percent,
        tokens_used=int(usage[0]),
        cost_usd=float(usage[1]),
        # 첫 LLM 콜이 끝나기 전(token_usage 0행)엔 상태 전이 시각(updated_at)으로 폴백 —
        # created→researching 직후 구간에서도 경과 시간이 새로고침에 초기화되지 않게.
        started_at=usage[2] or (project.updated_at if project.status != "created" else None),
        last_activity_at=usage[3],
        queue_position=(queue_status(project.id) or {}).get("position"),
        active_step=_active_step_label(project.status, project.id),
        active_steps=_active_step_labels(project.status, project.id),
        active_seconds=active_seconds,
        source_target=settings.research_min_sources,
        runner_alive=is_running(project.id),
        last_event_at=last_event_at(project.id),
    )


def _active_step_label(status: str, project_id: UUID) -> str | None:
    """실행 중 프로젝트의 최근 세부 단계 라벨 — 스테퍼 서브라벨용(인메모리, 단일 워커)."""
    if status not in ("researching", "indexing", "writing", "reviewing"):
        return None
    event = last_step(project_id)
    if event is None or event.get("status") != "started":
        return None
    return str(event.get("step") or "") or None


def _active_step_labels(status: str, project_id: UUID) -> list[str] | None:
    """진행 중 세부 단계 전부 — 병렬 작성(세마포어 4)의 절 4개가 다 보이게.

    마지막 하나만 보여주면 "2.5만 돌고 2.2~2.4는 멈췄나"로 읽힌다(2026-08-15 지적).
    """
    if status not in ("planning", "researching", "indexing", "writing", "reviewing"):
        return None
    labels = active_steps(project_id)
    return labels or None


# 설정을 얼리는 상태 — 완료·보관은 '다음 단계'가 없어 저장이 반영될 자리가 없다.
# 취소·실패는 재개 대상이라 얼리지 않는다(사람이 옵션을 고쳐 다시 돌린다).
_CONFIG_FROZEN_STATUSES = (
    ProjectStage.COMPLETED.value,
    ProjectStage.ARCHIVED.value,
)


@router.patch("/{project_id}/config", response_model=ProjectRead)
async def update_project_config(
    project_id: UUID,
    data: ConfigUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
) -> Project:
    """진행 중 프로젝트의 옵션(config) 전체 교체.

    다음 단계부터 반영된다(이미 지나간 단계는 재실행하지 않음). 제목·주제는
    생성 후 변경 불가(프론트 edit 모드도 readonly).

    **완료·보관된 프로젝트는 거부한다.** 다음 단계가 없으므로 저장은 아무 일도 못 하는
    시늉이다(재개(reopen) 경로가 이 동결을 푸는 유일한 문이 된다).

    목차가 바뀌면: plan 정본을 절 id 기준으로 병합 재생성하고(merge_config_update),
    이미 저장된 절 행의 번호·제목도 새 목차에 맞춰 재정렬한다(sync_rows_to_plan) —
    절 정체성은 outline의 안정 id가 지키므로 삽입·삭제·이동이 다른 절의 계획·본문을
    건드리지 않는다(2026-08-21 절 정체성 수술).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if project.status in _CONFIG_FROZEN_STATUSES:
        raise ValidationError(
            message="완료된 보고서의 설정은 바꿀 수 없습니다",
            code="PROJECT_CONFIG_FROZEN",
        )
    normalized_config = _validate_outline_config(
        data.config, await _known_analyst_names(session, current_user.id)
    )
    await _validate_rules_config(session, current_user.id, normalized_config)
    outline_changed = normalized_config.get("outline") != (project.config or {}).get("outline")
    # 옵션 교체가 파이프라인이 남긴 내부 키까지 지우면 안 된다 - 취소 복귀 지점과
    # 검증 경고 완료 표시·모델 스냅샷은 사용자가 폼에서 만지는 값이 아니다.
    project.config = merge_config_update(project.config, normalized_config)
    if outline_changed:
        from src.services.sections.store import sync_rows_to_plan

        plan = plan_from_config(project.config)
        if plan:
            await sync_rows_to_plan(session, project.id, plan)
    await session.flush()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
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
    # 렌더 버전이 파일명에 붙으므로 남은 버전을 전부 훑어 지운다(옛 버전 잔재 포함).
    try:
        for stale in Path(settings.export_dir).glob(export_file_pattern(project.id)):
            stale.unlink(missing_ok=True)
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
        indexing = bool(meta.get("indexing"))
        deferred = bool(meta.get("index_deferred"))
        index_error = meta.get("index_error")
        if indexing:
            preview = f"{label} · 색인 중… (수백 페이지 PDF는 몇 분 걸립니다)"
        elif index_error:
            preview = f"{label} · 색인 실패"
        elif deferred and not chunks:
            preview = f"{label} · 실행 시 색인됩니다"
        else:
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
            library_node_id=row.library_node_id,
            indexing=indexing,
            index_deferred=deferred,
            index_error=index_error,
            size_bytes=meta.get("size_bytes"),
            page_count=meta.get("page_count"),
            n_chunks=chunks,
            created_at=row.created_at,
            published_year=meta.get("published_year"),
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
        # 웹은 색인 전에도 수집이 준 page_age에서 연도를 파생해 보여준다(2026-08-17)
        published_year=meta.get("published_year") or year_from_page_age(meta.get("page_age")),
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
    current_user: Annotated[User, Depends(require_writer)],
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
    changed = row.is_included != data.is_included
    row.is_included = data.is_included
    await session.flush()
    if changed:
        # 채택 토글은 검색 결과를 바꾼다(검색 SQL이 제외분을 거른다) — 리허설 캐시
        # 무효화. 색인 전 토글에는 캐시가 아직 없지만 +1은 무해하다(값 자체 무의미).
        from src.services.retrieval.rehearsal import bump_index_version

        await bump_index_version(project.id)
    return _to_source_item(row)


@router.delete(
    "/{project_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project_source(
    project_id: UUID,
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
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
    # 리허설 재개방으로 RESEARCHING에 돌아온 프로젝트는 이미 청크가 있다 — 삭제가
    # CASCADE로 청크를 지우므로 리허설 캐시를 무효화한다(색인 전 삭제엔 무해한 +1).
    from src.services.retrieval.rehearsal import bump_index_version

    await bump_index_version(project.id)


# 색인 백그라운드 태스크 참조 — GC로 사라지지 않게 붙잡아 둔다.
_INDEX_TASKS: set[asyncio.Task] = set()
# 동시 색인 상한. PDF 파싱(docling 레이아웃 모델)은 파일당 수 GB를 쓴다 — 여러 건을
# 한꺼번에 돌리면 메모리가 터지고 **조용히 저품질 파서로 폴백**한다. 2026-08-20 실측:
# 13건을 한 번에 올렸더니 9건이 OSError 1455(페이징 파일 부족)로 docling에 실패해
# pymupdf4llm으로 떨어졌다 — 표 구조가 빠져 수치 근거가 통째로 약해진다(v2 업로드
# 청크의 11.8%가 표였다). 실패가 예외가 아니라 폴백이라 화면에는 정상으로 보였다.
# 1로 두는 이유: 순차면 각 파일이 메모리를 다 쓸 수 있어 docling이 성공한다. 총 시간은
# 늘지만 색인은 백그라운드라 사용자를 막지 않는다.
_INDEX_SEMAPHORE = asyncio.Semaphore(1)

# 색인 유예 상태 — 아직 런의 색인 단계가 앞에 남아 있어 업로드 파싱·임베딩을 거기로
# 미룬다. indexing(단계 진행 중)·완성 이후는 즉시 색인(자료 보강 경로는 런을 안 돈다).
_INDEX_DEFER_STATUSES = ("created", "planning", "researching")


async def _index_in_background(source: SourceInput, error_context: str) -> None:
    """업로드/라이브러리 자료를 요청 밖에서 색인하고 결과를 행 메타에 남긴다.

    수백 페이지 PDF는 파싱·임베딩에 수 분이 걸린다. 요청 안에서 처리하면 프론트
    타임아웃에 걸려 '실패'로 보이지만 실제로는 뒤에서 끝나 있었다(2026-08-10 지적).
    placeholder 행을 먼저 만들고 여기서 채우므로 화면은 '색인 중'을 보여줄 수 있다.

    동시 실행은 _INDEX_SEMAPHORE로 1건씩 직렬화한다(위 주석의 메모리 폭주 실측).
    """
    from src.services.indexing.vector import build_vector_indexing_service

    error: str | None = None
    indexed: object | None = None
    try:
        async with _INDEX_SEMAPHORE:
            indexed = await build_vector_indexing_service().index_source(source)
    except Exception as exc:
        error = _index_error_message(exc)
        logger.warning("source.index_failed_bg", context=error_context, exc_info=True)
    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(ProjectSource).where(
                    ProjectSource.project_id == source.project_id,
                    ProjectSource.upload_path == source.upload_path
                    if source.upload_path
                    else ProjectSource.library_node_id == source.library_node_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            meta = dict(row.metadata_ or {})
            meta["indexing"] = False
            if indexed is not None:
                meta.update(_index_meta(source, indexed))
            if error:
                meta["index_error"] = error
            else:
                meta.pop("index_error", None)
            row.metadata_ = meta
            # 결과 객체가 아니라 DB 청크 수로 판정 — 파싱 실패(indexed=None) 재색인은
            # 기존 청크를 지우지 않아, 이전에 성공한 자료를 오인 제외하면 안 된다.
            n_chunks = (
                await session.execute(
                    select(func.count()).select_from(Chunk).where(Chunk.source_id == row.id)
                )
            ).scalar_one()
            apply_index_outcome(row, int(n_chunks))
            await session.commit()


def _index_error_message(exc: BaseException) -> str:
    """색인 실패를 사람이 읽을 수 있는 한 줄로(원인 코드는 노출하지 않는다)."""
    from src.clients.parser import UnsupportedFormatError

    if isinstance(exc, UnsupportedFormatError):
        return "지원하지 않는 파일 형식입니다(PDF·HWPX·DOCX·MD·TXT만 색인할 수 있습니다)."
    if isinstance(exc, BadZipFile):
        return (
            "HWPX/DOCX 형식이 아닙니다. 확장자만 바꾼 구형 .hwp 파일일 수 있습니다 — "
            "한컴오피스에서 열어 '다른 이름으로 저장 → HWPX(*.hwpx)'로 변환해 주세요."
        )
    return "자료를 색인하지 못했습니다. 파일이 손상되지 않았는지 확인해 주세요."


async def _placeholder_source(session: AsyncSession, source: SourceInput) -> ProjectSource:
    """색인 전 자리 행 — 화면이 즉시 목록에 띄우고 '색인 중'을 표시한다."""
    row = (
        await session.execute(
            select(ProjectSource).where(
                ProjectSource.project_id == source.project_id,
                ProjectSource.upload_path == source.upload_path
                if source.upload_path
                else ProjectSource.library_node_id == source.library_node_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProjectSource(
            project_id=source.project_id,
            library_node_id=source.library_node_id,
            upload_path=source.upload_path,
            source_type=source.source_type,
            title=source.title,
            url=source.url,
            reliability=source.reliability,
        )
        session.add(row)
    row.metadata_ = {**(row.metadata_ or {}), "indexing": True, "index_error": None}
    row.metadata_.pop("index_error")
    await session.flush()
    await session.refresh(row)
    return row


def _index_meta(source: SourceInput, result: object) -> dict:
    """색인 결과에서 자료 메타를 만든다 — 라이브러리 목록의 크기·페이지 열이 이걸 읽는다.

    업로드 자료는 본문(content_md)이 없고 디스크에 파일로 있어, 크기를 여기서
    재두지 않으면 목록에 0 B로 뜬다(상위 폴더 합계도 0). 페이지 수는 파서만 안다.
    """
    meta: dict = {
        "origin": source.source_type,
        "chunks": getattr(result, "chunks_created", 0),
    }
    pages = getattr(result, "page_count", None)
    if pages:
        meta["page_count"] = pages
    # 발간연도 — 자료 검토 게이트·통계의 연도 배지가 자료 단위 값을 읽는다(2026-08-17).
    year = getattr(result, "published_year", None)
    if year:
        meta["published_year"] = year
    # 파서 정체·경고 — 전에는 로그에만 남아 pymupdf 폴백(표가 평문으로 뭉개진 자료)이
    # 화면에서 정상으로 보였다(2026-08-20 실사고). 이제 목록이 저품질 파싱을 표시할
    # 수 있다. 경고가 없으면 키 자체를 만들지 않아 메타를 어지럽히지 않는다.
    parser_name = getattr(result, "parser_name", "")
    if parser_name:
        meta["parser_name"] = parser_name
    parse_warnings = getattr(result, "parse_warnings", None)
    if parse_warnings:
        meta["parse_warnings"] = list(parse_warnings)
    try:
        meta["size_bytes"] = Path(source.file_path).stat().st_size
    except OSError:
        pass
    return meta


@router.post(
    "/{project_id}/sources/upload",
    response_model=SourceItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_source(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
    file: Annotated[UploadFile, File()],
) -> SourceItemRead:
    """직접 업로드 자료 — 파일 저장 후 즉시 색인(청킹·임베딩).

    채택(is_included) 기본 참. 나중에 제외하면 검색 시점에 자동으로 근거에서 빠진다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    # 빈 파일 검사는 read_validated_upload가 한다(라이브러리 업로드와 같은 검증을 쓰려고
    # 공용 검증기로 올렸다 - 한쪽에만 있어서 0바이트 파일이 라이브러리로 들어왔다).
    safe_name, content = await read_validated_upload(file, max_bytes=MAX_SOURCE_UPLOAD_BYTES)
    dest_dir = Path(settings.library_dir) / "project_sources" / str(project.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid4()}_{safe_name}"
    dest.write_bytes(content)
    source = SourceInput(
        project_id=project.id,
        source_type="upload",
        file_path=dest,
        upload_path=str(dest),
        title=safe_name,
    )
    # 수백 페이지 PDF는 파싱·임베딩이 수 분이다 — 요청 안에서 처리하면 프론트
    # 타임아웃에 걸려 '실패'로 보인다(실제로는 뒤에서 끝나 있었다). 자리 행을 먼저
    # 돌려주고 색인은 뒤에서 돈다.
    row = await _placeholder_source(session, source)
    if project.status in _INDEX_DEFER_STATUSES:
        # 실행 전 업로드는 색인을 런의 색인 단계로 미룬다(2026-08-20 사용자 결정 —
        # 색인의 소유자를 한 곳으로). 업로드는 파일 저장만으로 즉시 끝나고, 파싱·
        # 임베딩은 자료 게이트 승인 뒤 확정된 자료에만 비용을 쓴다. 완성 후
        # 자료 보강 업로드는 런이 색인 단계를 다시 밟지 않으므로 종전대로 즉시 색인.
        meta = dict(row.metadata_ or {})
        meta["indexing"] = False
        meta["index_deferred"] = True
        row.metadata_ = meta
        await session.commit()
        return _to_source_item(row)
    await session.commit()
    task = asyncio.create_task(_index_in_background(source, f"upload:{project.id}:{safe_name}"))
    _INDEX_TASKS.add(task)
    task.add_done_callback(_INDEX_TASKS.discard)
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
    current_user: Annotated[User, Depends(require_writer)],
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
    # 개인 소유뿐 아니라 회사 공유 자료의 역할/사용자 가시성 제한(visible_to_roles/users)까지
    # 검사한다. 다운로드 경로와 동일한 규칙을 재사용해 첨부로 우회되지 않게 한다.
    from src.api.routers.library import _can_view

    if not _can_view(node, current_user):
        raise AuthorizationError(message="이 자료에 접근할 수 없습니다.", code="FORBIDDEN")
    if not node.file_path or not Path(node.file_path).is_file():
        raise ValidationError(
            message="원본 파일이 없어 불러올 수 없습니다(수집 원문만 있는 자료일 수 있습니다).",
            code="LIBRARY_FILE_MISSING",
        )
    source = SourceInput(
        project_id=project.id,
        source_type="library",
        file_path=Path(node.file_path),
        library_node_id=node.id,
        title=node.name,
    )
    # 업로드와 같은 패턴으로 백그라운드 색인한다(2026-08-20 사용자 결정). 전에는
    # 요청 안에서 동기 색인했는데 두 가지가 문제였다: (1) _INDEX_SEMAPHORE 밖이라
    # 업로드 색인과 docling이 **동시에** 돌 수 있었다 - 정확히 메모리 폭주 사고의
    # 재현 조건. (2) 수백 페이지 PDF는 프론트 타임아웃에 걸려 실패로 보였다.
    # 프론트는 이미 indexing/index_error 메타를 읽고 4초 폴링하므로 그대로 동작한다.
    # 형식 오류(UnsupportedFormat 등)도 _index_error_message가 메타로 변환한다.
    row = await _placeholder_source(session, source)
    if project.status in _INDEX_DEFER_STATUSES:
        # 업로드 경로와 같은 유예 규칙(비대칭 수리 2026-08-21) — 실행 전 첨부는 색인을
        # 런의 색인 단계로 미룬다. 여기만 즉시 색인하면 자료 게이트 전에 임베딩 비용이
        # 나가고, 게이트에서 제외해도 청크가 이미 만들어져 있었다.
        meta = dict(row.metadata_ or {})
        meta["indexing"] = False
        meta["index_deferred"] = True
        row.metadata_ = meta
        await session.commit()
        return _to_source_item(row)
    await session.commit()
    task = asyncio.create_task(_index_in_background(source, f"library:{project.id}:{node.id}"))
    _INDEX_TASKS.add(task)
    task.add_done_callback(_INDEX_TASKS.discard)
    return _to_source_item(row)


@router.get("/{project_id}/verify-report", response_model=list[VerifyFindingRead])
async def get_verify_report(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[VerifyFindingRead]:
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
    resolved = _resolved_keys(project)
    return [_to_finding_read(row, resolved) for row in rows]


# 재검증이 도는 프로젝트 - 단일 워커 전제(진행 WebSocket과 같은 가정).
_VERIFYING: set[UUID] = set()
_VERIFY_TASKS: set[asyncio.Task] = set()


def _finding_key(chapter: int, category: str, section_ref: str | None, detail: str) -> str:
    """경고의 지문 - 재검증이 행을 전량 교체해도 '완료 표시'가 살아남게 하는 열쇠.

    id는 매번 새로 생기므로 쓸 수 없다. 내용이 같으면 같은 경고로 본다.
    """
    raw = f"{chapter}|{category}|{section_ref or ''}|{detail}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _resolved_keys(project: Project) -> set[str]:
    raw = (project.config or {}).get("verify_resolved")
    return {str(k) for k in raw} if isinstance(raw, list) else set()


def _to_finding_read(row: VerifyFinding, resolved: set[str]) -> VerifyFindingRead:
    key = _finding_key(row.chapter_number, row.category, row.section_ref, row.detail)
    return VerifyFindingRead(
        id=row.id,
        chapter_number=row.chapter_number,
        severity=row.severity,
        category=row.category,
        section_ref=row.section_ref,
        section_id=row.section_id,
        detail=row.detail,
        created_at=row.created_at,
        key=key,
        resolved=key in resolved,
    )


async def _reverify_in_background(project_id: UUID) -> None:
    """저장된 본문으로 PM 검증을 다시 돌린다(요청 밖).

    챕터당 1콜이라 35절 보고서는 수십 초~수 분이 걸린다 - 요청 안에서 처리하면
    프론트가 타임아웃으로 실패로 읽는다(업로드 색인에서 겪은 것과 같은 문제).
    """
    from src.services.qa.pm_verify import run_pm_verify
    from src.workflows.stages import _models_for

    try:
        async with async_session_maker() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            rows = await _load_sections(session, project_id)
        if not rows:
            return
        state = _state_for_export(project, rows)
        await run_pm_verify(state, model=_models_for(state)["verify"])
    except Exception:
        logger.warning("verify.rerun_failed", project_id=str(project_id), exc_info=True)
    finally:
        _VERIFYING.discard(project_id)


@router.post("/{project_id}/verify-report", status_code=status.HTTP_202_ACCEPTED)
async def rerun_verify_report(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
) -> dict[str, bool]:
    """PM 검증 다시 실행 - 편집으로 고친 뒤 남은 경고를 다시 보기 위한 경로.

    검증은 조립 때 한 번만 돌아서, 지적을 고쳐도 경고가 그대로 남아 있었다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if project.id in _VERIFYING:
        return {"started": False, "running": True}
    _VERIFYING.add(project.id)
    task = asyncio.create_task(_reverify_in_background(project.id))
    _VERIFY_TASKS.add(task)
    task.add_done_callback(_VERIFY_TASKS.discard)
    return {"started": True, "running": True}


@router.get("/{project_id}/verify-report/status")
async def verify_report_status(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict[str, bool]:
    """재검증이 도는 중인지 - 화면이 폴링을 멈출 시점을 안다."""
    project = await _get_authorized_project(project_id, session, current_user)
    return {"running": project.id in _VERIFYING}


@router.patch("/{project_id}/verify-report/{finding_id}", response_model=VerifyFindingRead)
async def resolve_verify_finding(
    project_id: UUID,
    finding_id: UUID,
    data: VerifyFindingResolve,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
) -> VerifyFindingRead:
    """경고 하나를 '처리함'으로 표시(또는 해제).

    표시는 행이 아니라 지문에 붙인다 - 재검증은 행을 전량 교체하므로 id에 붙이면
    다시 돌리는 순간 사라진다. 같은 내용의 경고가 다시 나오면 표시도 따라온다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = await session.get(VerifyFinding, finding_id)
    if row is None or row.project_id != project.id:
        raise NotFoundError(message="검증 경고를 찾을 수 없습니다", code="FINDING_NOT_FOUND")
    key = _finding_key(row.chapter_number, row.category, row.section_ref, row.detail)
    keys = _resolved_keys(project)
    if data.resolved:
        keys.add(key)
    else:
        keys.discard(key)
    project.config = {**(project.config or {}), "verify_resolved": sorted(keys)}
    await session.flush()
    return _to_finding_read(row, keys)


def _state_for_export(project: Project, rows: list[Section], author: str = "") -> ProjectState:
    """sections 테이블의 확정 본문 → export_report가 먹는 최소 상태.

    후보/선택 구조를 절당 1후보로 재구성한다 — 편집된 최신 본문이 곧 선택본.
    """
    # 절 계획의 부수 정보(장 제목·방향·에이전트)는 config 정본에서 가져온다 — 제목만
    # 담아 만들면 렌더가 장 맥락을 잃는다. 정본이 없는 옛 프로젝트는 행 값으로 채운다.
    canonical = {p.section_id: p for p in plan_from_config(project.config)}
    plans: list[SectionPlan] = []
    sets: list[SectionCandidateSet] = []
    selections: dict[UUID, UUID] = {}
    for row in rows:
        plan = canonical.get(row.id) or SectionPlan(
            section_id=row.id,
            chapter_number=row.chapter_number,
            section_number=row.section_number,
            title=row.title,
            chapter_title=row.chapter_title or "",
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
        title=project.title,
        author=author,
        # 표지 작성일 — 프로젝트 생성 시각(기본값 now()로 두면 다운로드 날짜가 찍힌다)
        created_at=project.created_at,
        preset=project.preset,
        section_plan=plans,
        section_candidates=sets,
        section_selections=selections,
        options=project.config or {},
    )


def _export_is_stale(path: Path, project: Project, rows: list[Section]) -> bool:
    """렌더된 파일이 최신 편집보다 오래됐는가 — 재렌더 여부의 유일한 판단.

    보수적으로 본다: 시각을 못 읽으면 오래된 것으로 쳐서 다시 만든다(낡은 파일을
    내주는 쪽이 느린 것보다 나쁘다).
    """
    try:
        rendered_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return True
    stamps = [project.updated_at, *(r.updated_at for r in rows if r.updated_at is not None)]
    latest = max((t for t in stamps if t is not None), default=None)
    if latest is None:
        return True
    if latest.tzinfo is None:  # 방어: naive면 UTC로 간주(저장은 UTC 규약)
        latest = latest.replace(tzinfo=UTC)
    return latest > rendered_at


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
    path = Path(settings.export_dir) / export_filename(project.id)
    rows = await _load_sections(session, project.id)
    # 이미 렌더된 파일이 마지막 편집보다 새것이면 다시 만들 이유가 없다. 35절 보고서
    # 재렌더는 수십 초라, 매번 돌리면 클릭 후 한참 아무 일도 안 일어나는 것처럼 보인다
    # (2026-08-10 지적). 편집·조립이 있으면 아래 재렌더 경로가 그대로 탄다.
    if rows and path.is_file() and not _export_is_stale(path, project, rows):
        return FileResponse(
            path,
            filename=f"{project.title}.hwpx",
            media_type="application/octet-stream",
        )
    if rows:
        try:
            from src.services.export.report import export_report

            owner = await session.get(User, project.owner_id)
            state = _state_for_export(project, rows, author=owner.name if owner else "")
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
        except PermissionError:
            # 표준 경로가 잠겨 있다(사용자가 한컴에서 그 파일을 열어둔 경우 흔하다).
            # 폴백으로 옛 파일을 내주면 방금 고친 본문·표지가 반영 안 된 채 조용히
            # 내려간다(2026-08-10 실측: 새 표지가 안 나옴). 임시 경로에 렌더해 서빙한다.
            try:
                tmp_dir = Path(settings.export_dir) / "_locked"
                path = export_report(state, output_dir=tmp_dir, glossary=project.glossary)
                logger.warning(
                    "export.rerender_to_temp", project_id=str(project.id), path=str(path)
                )
            except Exception:
                logger.warning("export.rerender_failed", project_id=str(project.id), exc_info=True)
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
    current_user: Annotated[User, Depends(require_writer)],
) -> RunResponse:
    project = await _get_authorized_project(project_id, session, current_user)
    pending = await get_pending_gate(session, project.id)
    if pending is None:
        raise ValidationError(message="대기 중인 검토 게이트가 없습니다", code="NO_PENDING_GATE")
    # 브리프 게이트가 목차를 고쳐 보냈다면 생성 시점과 같은 잣대로 검증·정규화한다 —
    # 여기서 막지 않으면 잘못된 목차가 config에 커밋되고 수집 도중에야 터진다.
    # 정규화(안정 id·builds_on 토큰)를 거쳐야 재플래닝이 절 정체성을 보존한다.
    if pending["gate"] == ReviewGate.DESIGN_BRIEF.value and "outline" in data.decision:
        normalized = _validate_outline_config(
            {"outline": data.decision["outline"]},
            await _known_analyst_names(session, current_user.id),
        )
        data.decision["outline"] = normalized["outline"]
    # 한도 사전 검사 — 재개 구간(색인·작성·추가 검색)도 LLM 비용이 크다.
    from src.clients.llm.quota_gate import check_user_quota

    await check_user_quota(project.owner_id)
    # 결정값으로 척추 재개 — 백그라운드. 게이트 다음 단계부터 이어서 진행된다.
    await resume_run(project.id, data.decision)
    # 재계산 라운드는 전진하지 않고 같은 게이트가 다시 열린다 — 응답 status도 그대로.
    replanning = (
        pending["gate"] == ReviewGate.DESIGN_BRIEF.value and data.decision.get("action") == "replan"
    )
    return RunResponse(
        project_id=str(project.id),
        status=ProjectStage.PLANNING if replanning else ProjectStage.RESEARCHING,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 재개·버전 — report_versions(append-only 스냅샷)이 원천 (2026-08-21 설계).
# 시차 작성: 완료 보고서를 다시 열어 자료를 보강하고 빈 절·새 절을 이어 쓴다.
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/{project_id}/reopen", response_model=ProjectRead)
async def reopen_project(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
) -> Project:
    """완료된 보고서를 다시 연다 — 현재 완성본을 버전으로 얼리고 자료 단계로 되돌린다.

    되돌리는 지점이 RESEARCHING인 이유: 다음 실행이 색인 단계부터 밟아
    ① 새로 올린 자료의 잔여 색인(증분 — 이미 청크 있는 자료는 스킵)과
    ② 검색 리허설(새 절·빈 절의 근거 충분성 판정, 공백이면 자료 게이트 재개방)을
    거친 뒤 작성으로 간다. 작성은 본문 있는 절을 건너뛰므로(증분 재개) 완성된
    장은 다시 쓰지 않는다. 상태 전이만 하고 실행은 하지 않는다 — 사람이 자료를
    올리고 목차를 고친 뒤 직접 시작한다(재개 즉시 실행하면 보강할 틈이 없다).

    completed_at 해제는 상태 전이 리스너가 처리한다(db/models/project.py).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if project.status != ProjectStage.COMPLETED.value:
        raise ValidationError(
            message="완료된 보고서만 다시 열 수 있습니다",
            code="PROJECT_NOT_REOPENABLE",
        )
    from src.services.sections.versions import snapshot_report

    # 재개 직전 보존 — 완료 후 수동 편집분까지 잡는 마지막 기회. 조립 직후 그대로면
    # 내용 지문이 같아 새 버전은 생기지 않는다.
    await snapshot_report(session, project.id, reason="reopen", created_by=current_user.id)
    project.status = ProjectStage.RESEARCHING.value
    await session.flush()
    await session.refresh(project)
    logger.info("project.reopened", project_id=str(project.id), user_id=str(current_user.id))
    return project


def _version_read(row: Any) -> ReportVersionRead:
    sections = row.sections or []
    return ReportVersionRead(
        version_no=row.version_no,
        reason=row.reason,
        created_at=row.created_at,
        n_sections=len(sections),
        total_chars=sum(len(s.get("content") or "") for s in sections),
    )


@router.get("/{project_id}/versions", response_model=list[ReportVersionRead])
async def list_report_versions(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> list[ReportVersionRead]:
    """버전 히스토리 — 최신부터. 커밋 로그처럼 사유·시각·규모만 싣는다(본문은 상세로)."""
    project = await _get_authorized_project(project_id, session, current_user)
    from src.db.models.report_version import ReportVersion

    rows = (
        (
            await session.execute(
                select(ReportVersion)
                .where(ReportVersion.project_id == project.id)
                .order_by(ReportVersion.version_no.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_version_read(r) for r in rows]


async def _get_version(session: AsyncSession, project_id: UUID, version_no: int):
    from src.db.models.report_version import ReportVersion

    row = (
        await session.execute(
            select(ReportVersion).where(
                ReportVersion.project_id == project_id,
                ReportVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(message="해당 버전이 없습니다", code="VERSION_NOT_FOUND")
    return row


@router.get("/{project_id}/versions/diff", response_model=VersionDiffResponse)
async def diff_report_versions(
    project_id: UUID,
    base: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    target: int | None = None,
) -> VersionDiffResponse:
    """버전 간 절 단위 비교 — target 생략 시 현재 작업 사본과 비교.

    매칭은 절 안정 id(정체성 수술 2026-08-21) — 번호가 밀려도 '수정'과 '이동'을
    오판하지 않는다. 문단 안 단어 색칠은 프론트 몫: 서버는 판정과 양쪽 본문만.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    from src.services.sections.versions import current_sections_snapshot, diff_sections

    base_row = await _get_version(session, project.id, base)
    if target is not None:
        target_sections = (await _get_version(session, project.id, target)).sections or []
    else:
        target_sections = await current_sections_snapshot(session, project.id)
    entries = diff_sections(base_row.sections or [], target_sections)
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return VersionDiffResponse(
        base_version=base,
        target_version=target,
        n_added=counts["added"],
        n_removed=counts["removed"],
        n_modified=counts["modified"],
        n_unchanged=counts["unchanged"],
        entries=[VersionDiffEntry.model_validate(e) for e in entries],
    )


@router.get("/{project_id}/versions/{version_no}", response_model=ReportVersionDetail)
async def get_report_version(
    project_id: UUID,
    version_no: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> ReportVersionDetail:
    """버전 상세 — 스냅샷 절 전량(읽기 전용 열람용)."""
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_version(session, project.id, version_no)
    head = _version_read(row)
    return ReportVersionDetail(
        **head.model_dump(),
        sections=[VersionSection.model_validate(s) for s in row.sections or []],
    )


@router.get("/{project_id}/versions/{version_no}/download")
async def download_report_version(
    project_id: UUID,
    version_no: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FileResponse:
    """버전 스냅샷을 HWPX로 렌더해 다운로드 — 파일은 보관하지 않고 내용에서 재생한다.

    버전 내용은 불변이라 렌더 결과를 버전별 폴더에 캐시한다(파일이 있으면 그대로
    서빙 — 렌더러 버전이 바뀌면 파일명이 갈려 자연히 재렌더). 출처 최종장은 현재
    채택 자료 목록으로 싣는다 — 재개 후 자료가 늘었으면 스냅샷 시점보다 넉넉한
    목록이 붙는 근사이고, 본문 인용 번호는 스냅샷 시점 값 그대로다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_version(session, project.id, version_no)
    out_dir = Path(settings.export_dir) / "_versions" / f"{project.id}.v{version_no}"
    path = out_dir / export_filename(project.id)
    filename = f"{project.title}.v{version_no}.hwpx"
    if path.is_file():
        return FileResponse(path, filename=filename, media_type="application/octet-stream")
    pseudo_rows = [
        Section(
            id=UUID(str(s["section_id"])),
            project_id=project.id,
            chapter_number=int(s["chapter_number"]),
            section_number=int(s["section_number"]),
            chapter_title=str(s.get("chapter_title") or ""),
            title=str(s.get("title") or ""),
            level=2,
            content=str(s.get("content") or ""),
            source_ids=[],
            meta={},
            qa_status="passed",
            status="completed",
        )
        for s in row.sections or []
    ]
    from src.services.export.report import export_report

    owner = await session.get(User, project.owner_id)
    state = _state_for_export(project, pseudo_rows, author=owner.name if owner else "")
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
    path = await asyncio.to_thread(
        export_report, state, output_dir=out_dir, glossary=project.glossary
    )
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


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
                evidence_scarce=bool((row.meta or {}).get("volume_scaled")),
            )
        )
        chapter_statuses[row.chapter_number].append(row.status)

    tree = [chapters[n] for n in sorted(chapters)]
    for n, node in zip(sorted(chapters), tree, strict=True):
        node.status = _reduce_chapter_status(chapter_statuses[n])
    return SectionTreeResponse(tree=tree)


@router.get("/{project_id}/source-usage", response_model=SourceUsageResponse)
async def get_source_usage(
    project_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceUsageResponse:
    """완성 보고서의 자료 사용 통계(전체/장/절) — 읽기 전용이라 뷰어도 본다.

    번호 해석이 조립 후 규약(전역 번호 = 채택 자료의 수집 순)이라, 조립 전에는
    통계가 성립하지 않아 제공하지 않는다(프론트도 완성 상태에서만 노출).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    if not _is_renumbered(project):
        raise ValidationError(
            message="자료 사용 통계는 보고서가 완성된 뒤에 제공됩니다",
            code="REPORT_NOT_ASSEMBLED",
        )
    rows = await _load_sections(session, project.id)
    sources_ordered = (
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
    return build_source_usage(rows, list(sources_ordered))


async def _get_section(session: AsyncSession, project_id: UUID, section_id: UUID) -> Section:
    row = await session.get(Section, section_id)
    if row is None or row.project_id != project_id:
        raise NotFoundError(message="섹션을 찾을 수 없습니다", code="SECTION_NOT_FOUND")
    return row


# 검토 중(writing·reviewing) 편집도 허용한다 — 통합 검토 화면(2026-08-07)의 정식
# 경로. 편집 결과는 resume 시 overlay_working_copy가 payload 후보보다 우선 반영한다.


# config 안에서 폼이 건드리지 않는 내부 키 - 옵션 전체 교체 때 살려 둔다.
# models = 런 시작 시점 역할별 모델 스냅샷(러너 기록) - 표시 라벨의 진실.
_INTERNAL_CONFIG_KEYS = (
    "cancelled_from",
    "verify_resolved",
    "models",
    # analysts = 런 시작 시점 DB 출신 에이전트 스냅샷(러너 기록) - 그 런이 실제로 쓴 페르소나.
    "analysts",
    SECTION_PLAN_KEY,
    "_design_plan",
    "_rehearsal_reopens",
)


def merge_config_update(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """옵션 전체 교체 + 내부 키 보존. 내부 키는 서버가 진실이라 클라이언트가
    같은 키를 되돌려 보내도(폼 round-trip) 서버 값이 이긴다.

    목차가 바뀌면 _section_plan을 **절 id 기준으로 병합 재생성**한다(2026-08-21) —
    같은 id의 절은 정체성(id·브리프 검색 질의)을 지키고 번호·방향·핵심 포인트는
    새 목차에서 다시 파생한다. 통째로 버리던 종전 방식은 collect가 옛 목차로
    실행하는 어긋남은 막았지만, 절 하나 고칠 때마다 전 절의 id가 리셋돼 리허설
    캐시·실행 계획·본문 행 연결이 전부 끊겼다. plan이 아직 없으면(브리프 전) 그대로
    없음 — 이후 단계가 outline의 안정 id로 만든다.

    _design_plan(절 id 키)은 살아남은 절만 남기고, 리허설 재개방 예산은 새로
    시작한다(절 구성이 달라지면 수집 공백 판단도 달라진다).
    """
    keep = {k: v for k, v in (current or {}).items() if k in _INTERNAL_CONFIG_KEYS}
    if incoming.get("outline") != (current or {}).get("outline"):
        old_plan = load_section_plan(keep.get(SECTION_PLAN_KEY))
        keep.pop(SECTION_PLAN_KEY, None)
        if old_plan:
            from src.services.generation.planner import merge_section_plan

            try:
                merged = merge_section_plan(old_plan, incoming.get("outline") or {})
                keep[SECTION_PLAN_KEY] = dump_section_plan(merged)
            except ValueError:
                # 빈 목차 등 — 검증이 먼저 막지만, 병합 실패가 저장을 죽이면 안 된다.
                merged = []
            alive = {str(p.section_id) for p in merged}
            design = keep.get("_design_plan")
            if isinstance(design, dict):
                pruned = {k: v for k, v in design.items() if k in alive}
                if pruned:
                    keep["_design_plan"] = pruned
                else:
                    keep.pop("_design_plan", None)
        else:
            keep.pop("_design_plan", None)
        # 목차가 바뀌면 절 구성이 달라진다 — 리허설 재개방 예산도 새로 시작.
        keep.pop("_rehearsal_reopens", None)
    return {**incoming, **keep}


# 근거 추적 응답 상한 — 안 쓰인 근거는 풀 전체(수십 개)라 목록·본문 길이를 자른다.
_MAX_UNUSED_EVIDENCE = 40
_UNUSED_PREVIEW_CHARS = 400
_MAX_UNCITED_SAMPLES = 20


def _citation_numbers(content: str) -> list[int]:
    """본문의 출처 번호를 등장 순서대로 중복 없이 추출((출처 n)·[n] 모두).

    작성 시점 cited_chunk_ids(→sections.source_ids)가 바로 이 순서로 저장되므로
    (candidates._extract_cited_ids), 위치 대응이 곧 번호↔출처 매핑이다.
    """
    return numbers_in_order(content)


def _citations_from_numbers(
    numbers: list[int],
    sources_ordered: list[ProjectSource],
) -> list[SectionCitation]:
    """전역 번호 → 채택 자료 목록(수집 순서) 직해석 — 조립 후에만 성립.

    조립 시 renumber가 본문 번호를 이 순서로 재매핑하므로(출처 최종장과 동일),
    번호 n = sources_ordered[n-1]이다.
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


async def _draft_citations(
    session: AsyncSession, row: Section, numbers: list[int]
) -> list[SectionCitation]:
    """조립 전(작성·검토 중) 라벨 — 로컬 번호를 인용 청크의 실제 자료로 푼다.

    조립 전의 [n]은 절-로컬 검색 풀 번호라 수집 순서 직해석이 틀린다 — 그렇게 풀면
    엉뚱한(이름만 비슷한) 자료가 라벨로 붙는다(2026-08-12 사용자 보고: 0청크 실패
    업로드가 색인된 웹 출처 라벨을 밀어냄). 작성 규약(candidates._extract_cited_ids:
    본문 고유 번호의 첫 등장 순서 = source_ids 저장 순서)으로 청크→자료를 직접 푼다.
    번호 자체는 조립 때 전역 번호로 바뀐다.
    """
    cited = list(row.source_ids or [])
    order = {n: i for i, n in enumerate(numbers)}  # 첫 등장 순서 = cited 인덱스
    src_by_chunk: dict[UUID, ProjectSource] = {}
    wanted = [cid for cid in cited[: len(numbers)] if cid]
    if wanted:
        pairs = (
            await session.execute(
                select(Chunk.id, ProjectSource)
                .join(ProjectSource, ProjectSource.id == Chunk.source_id)
                .where(Chunk.id.in_(wanted))
            )
        ).all()
        src_by_chunk = {cid: src for cid, src in pairs}
    citations: list[SectionCitation] = []
    for n in sorted(numbers):
        i = order[n]
        src = src_by_chunk.get(cited[i]) if i < len(cited) else None
        if src is None:
            # 요약(RAPTOR) 청크는 원 자료가 없고, 조립 때 마커가 제거된다.
            citations.append(SectionCitation(number=n, title="(조립 후 번호가 확정됩니다)"))
        else:
            citations.append(
                SectionCitation(
                    number=n,
                    title=src.title or src.url or "(제목 없음)",
                    url=src.url,
                    source_id=str(src.id),
                    reliability=src.reliability,
                )
            )
    return citations


def _is_renumbered(project: Project) -> bool:
    """본문 인용 번호가 출처장 기준으로 확정됐는가 — 조립(assemble)을 지났는가.

    renumber는 assemble 단계에서 돈다(workflows/stages.py). 그 전에는 절-로컬
    번호라 채택 자료 수를 넘을 수 있다.
    """
    return project.status in (ProjectStage.COMPLETED.value, ProjectStage.ARCHIVED.value)


async def _section_citations(
    session: AsyncSession, row: Section, *, renumbered: bool = True
) -> list[SectionCitation]:
    """본문의 인용 번호 [n]을 출처(제목·URL·신뢰도) 목록으로 푼다.

    조립 후: 번호 체계 = 채택 자료의 수집 순서(renumber·출처 최종장과 동일 단일 진실).
    조립 전: 번호가 절-로컬이라 그 직해석이 성립하지 않는다 — 청크 규약으로 푼다.
    """
    numbers = _citation_numbers(row.content or "")
    if not numbers:
        return []
    if not renumbered:
        return await _draft_citations(session, row, numbers)
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


def _evidence_info(row: Section) -> EvidenceInfo:
    """작성 시 기록한 절 지표(sections.meta) → 화면용 플래그. 옛 절은 기록이 없어 빈 값."""
    meta = row.meta or {}
    count = meta.get("evidence_count")
    return EvidenceInfo(
        count=count if isinstance(count, int) else None,
        scarce=bool(meta.get("volume_scaled")),
        plan_failed=bool(meta.get("plan_failed")),
    )


def _section_content(
    row: Section,
    citations: list[SectionCitation] | None = None,
) -> SectionContentResponse:
    return SectionContentResponse(
        id=str(row.id),
        title=row.title,
        content=row.content,
        source_ids=[str(s) for s in row.source_ids],
        qa_status=row.qa_status,
        level=row.level,
        citations=citations or [],
        evidence=_evidence_info(row),
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
    return _section_content(
        row,
        await _section_citations(session, row, renumbered=_is_renumbered(project)),
    )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


@router.get("/{project_id}/sections/{section_id}/evidence", response_model=SectionEvidenceResponse)
async def get_section_evidence(
    project_id: UUID,
    section_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SectionEvidenceResponse:
    """이 절의 문장이 어느 근거 원문에서 나왔는지 되짚는다.

    출처 표기(자료 단위)만으로는 검증이 안 된다 - 자료를 열어 사람이 다시 찾아야 한다.
    여기서는 작성 때 프롬프트에 실린 청크 원문을 그대로 돌려주고, 인용되지 않은 채
    실려 있던 근거도 함께 내려 "안 보고 쓴 주장"과 "보고도 안 쓴 근거"를 가른다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_section(session, project.id, section_id)

    mapping, traceable = marker_chunk_ids(
        row.content or "", list(row.source_ids or []), row.meta, renumbered=_is_renumbered(project)
    )
    cited_order: list[UUID] = []
    number_of: dict[UUID, int] = {}
    for number in sorted(mapping):
        for cid in mapping[number]:
            if cid not in number_of:
                number_of[cid] = number
                cited_order.append(cid)
    if not traceable:
        # 번호 대응은 포기해도 "이 절이 인용한 근거가 무엇인가"는 정확히 남아 있다.
        # 번호 없이 목록만 보여준다 - 사람이 대조할 원문은 그대로 쓸모가 있다.
        cited_order = [UUID(str(s)) for s in (row.source_ids or []) if _is_uuid(str(s))]
    cited_set = set(cited_order)

    pool_raw = (row.meta or {}).get("pool_chunk_ids")
    pool = [UUID(s) for s in pool_raw if _is_uuid(str(s))] if isinstance(pool_raw, list) else []
    unused = [cid for cid in pool if cid not in cited_set][:_MAX_UNUSED_EVIDENCE]

    wanted = cited_order + unused
    if not wanted:
        units = uncited_units(row.content or "")
        return SectionEvidenceResponse(
            section_id=str(row.id),
            pool_size=len(pool),
            uncited_count=len(units),
            uncited_samples=units[:_MAX_UNCITED_SAMPLES],
            traceable=False,
        )

    chunk_rows = (
        await session.execute(
            select(
                Chunk.id, Chunk.content, Chunk.source_id, Chunk.chunk_index, Chunk.metadata_
            ).where(Chunk.id.in_(wanted))
        )
    ).all()
    by_id = {r[0]: r for r in chunk_rows}
    source_rows = (
        await session.execute(
            select(ProjectSource).where(
                ProjectSource.id.in_([r[2] for r in chunk_rows if r[2] is not None])
            )
        )
    ).scalars()
    sources = {s.id: s for s in source_rows}

    items: list[EvidenceChunk] = []
    for cid in wanted:
        found = by_id.get(cid)
        if found is None:
            continue  # 자료가 삭제된 경우 - 조용히 건너뛴다(본문 마커는 남아 있을 수 있다)
        _, content, source_id, chunk_index, meta = found
        src = sources.get(source_id) if source_id else None
        is_cited = cid in cited_set
        header = (meta or {}).get("header_path")
        page = (meta or {}).get("page")
        items.append(
            EvidenceChunk(
                number=number_of.get(cid),
                chunk_id=str(cid),
                # 인용된 근거는 원문 그대로(대조가 목적), 안 쓰인 근거는 앞부분만.
                content=content if is_cited else content[:_UNUSED_PREVIEW_CHARS],
                cited=is_cited,
                source_id=str(source_id) if source_id else None,
                source_title=(src.title or src.url) if src else None,
                url=src.url if src else None,
                reliability=src.reliability if src else None,
                header_path=[str(h) for h in header] if isinstance(header, list) else [],
                chunk_index=chunk_index,
                page=page if isinstance(page, int) else None,
            )
        )

    units = uncited_units(row.content or "")
    claims = _claim_rows(row, mapping, traceable, {r[0]: r[1] for r in chunk_rows}, cited_order)
    return SectionEvidenceResponse(
        section_id=str(row.id),
        items=items,
        claims=claims,
        pool_size=len(pool),
        cited_count=sum(1 for i in items if i.cited),
        unused_count=sum(1 for i in items if not i.cited),
        uncited_count=len(units),
        uncited_samples=units[:_MAX_UNCITED_SAMPLES],
        aligned_count=sum(1 for c in claims if c.status == "aligned"),
        weak_count=sum(1 for c in claims if c.status == "weak"),
        unmatched_count=sum(1 for c in claims if c.status == "unmatched"),
        traceable=traceable,
    )


def _claim_rows(
    row: Section,
    mapping: dict[int, list[UUID]],
    traceable: bool,
    chunk_texts: dict[UUID, str],
    cited_order: list[UUID],
) -> list[ClaimAlignmentRead]:
    """문장별 근거 대조 - 마커가 청크까지 안 풀리는 옛 절은 인용 근거 전체를 후보로 둔다.

    후보가 넓어지면 "어느 마커의 근거인지"는 못 말해도 "이 절의 근거 중 이 대목이
    가장 가깝다"는 말할 수 있다. 되짚기를 포기하는 것보다 사람에게 쓸모가 있다.
    """
    numbers = _citation_numbers(row.content or "")
    marker_chunks = mapping if traceable else {n: cited_order for n in numbers}
    out: list[ClaimAlignmentRead] = []
    for a in align_section(row.content or "", chunk_texts, marker_chunks):
        span = a.span
        out.append(
            ClaimAlignmentRead(
                claim=a.claim,
                numbers=a.numbers,
                status=a.status,
                chunk_id=str(span.chunk_id) if span else None,
                span_start=span.start if span else None,
                span_end=span.end if span else None,
                span_text=span.text if span else None,
                score=span.score if span else 0.0,
                ungrounded=a.ungrounded,
                grounded=[
                    GroundedNumberRead(
                        token=g.token,
                        chunk_id=str(g.chunk_id),
                        start=g.start,
                        end=g.end,
                        # 표 행이면 수백 자다 - 점프는 오프셋으로 하니 표시용만 남긴다
                        text=g.text[:200],
                    )
                    for g in a.grounded
                ],
            )
        )
    return out


@router.get("/{project_id}/sources/{source_id}/document", response_model=SourceDocumentResponse)
async def get_source_document(
    project_id: UUID,
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceDocumentResponse:
    """자료의 색인 본문을 원문 순서 청크로 반환 - 근거 패널의 원문 뷰어용.

    파일 재파싱 없이(PDF는 수십 초) 색인 청크를 이어 붙인다. 근거 추적의 span
    오프셋이 청크 기준이라 청크 경계를 살려 내려야 화면이 대목으로 점프한다.
    라이브러리의 content_md 뷰어(owner 한정·웹 전용)와 달리 프로젝트 열람 권한
    기준이고 업로드·라이브러리 자료도 다룬다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    src = (
        await session.execute(
            select(ProjectSource).where(
                ProjectSource.id == source_id, ProjectSource.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    rows = (
        await session.execute(
            select(Chunk.id, Chunk.content, Chunk.chunk_index, Chunk.metadata_)
            .where(Chunk.source_id == src.id, Chunk.track == "content")
            .order_by(Chunk.chunk_index.asc().nulls_last())
        )
    ).all()
    chunks: list[SourceChunkRead] = []
    for cid, content, idx, meta in rows:
        header = (meta or {}).get("header_path")
        page = (meta or {}).get("page")
        chunks.append(
            SourceChunkRead(
                chunk_id=str(cid),
                content=content,
                chunk_index=idx,
                header_path=[str(h) for h in header] if isinstance(header, list) else [],
                page=page if isinstance(page, int) else None,
            )
        )
    return SourceDocumentResponse(
        source_id=str(src.id),
        title=src.title,
        url=src.url,
        source_type=src.source_type,
        chunks=chunks,
    )


@router.get("/{project_id}/sources/{source_id}/file")
async def get_source_file(
    project_id: UUID,
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FileResponse:
    """자료의 원본 파일을 브라우저에서 열게 서빙 - "PDF 원본 p.N 열기"의 대상.

    쿠키 인증(access_token)이 있어 새 탭 <a href>로 바로 연다. filename을 주지
    않아 Content-Disposition이 attachment가 되지 않게 한다(다운로드가 아니라
    브라우저 PDF 뷰어로 열려야 #page=N 프래그먼트가 동작한다).
    """
    project = await _get_authorized_project(project_id, session, current_user)
    src = (
        await session.execute(
            select(ProjectSource).where(
                ProjectSource.id == source_id, ProjectSource.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    path: Path | None = None
    if src.upload_path:
        path = Path(src.upload_path)
    elif src.library_node_id is not None:
        node = await session.get(LibraryNode, src.library_node_id)
        if node is not None and node.file_path:
            path = Path(node.file_path)
    if path is None or not path.is_file():
        raise NotFoundError(
            message="원본 파일이 없습니다(웹 수집 자료이거나 파일이 정리됨)",
            code="SOURCE_FILE_MISSING",
        )
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else None
    return FileResponse(path, media_type=media_type or "application/octet-stream")


@router.patch("/{project_id}/sections/{section_id}", response_model=SectionContentResponse)
async def update_section_content(
    project_id: UUID,
    section_id: UUID,
    data: SectionContentUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
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
    return _section_content(
        row,
        await _section_citations(session, row, renumbered=_is_renumbered(project)),
    )


def _plan_for_row(project: Project, row: Section) -> SectionPlan:
    """저장된 절 → 작성 계획. **절 id로** 계획 정본(_section_plan)에서 되살린다.

    제목만 담아 넘기면 재작성이 페르소나도 분량 목표도 없이 쓴다 - 같은 절인데 처음
    작성한 글과 성격이 달라진다. 종전에는 목차 배열 **위치**로 찾아서, 절을 더하거나
    지우면 그 뒤 절들이 다른 절의 계획으로 재작성됐다(경고 없이) — 안정 id 수술로
    이 계열이 소거됐다(2026-08-21). 정본이 없으면 outline(안정 id)에서 파생하고,
    그래도 못 찾으면 행이 가진 값만으로 쓴다(장 맥락은 검색 질의가 쓴다).
    """
    for candidate in plan_from_config(project.config):
        if candidate.section_id == row.id:
            if not candidate.chapter_title and row.chapter_title:
                return candidate.model_copy(update={"chapter_title": row.chapter_title})
            return candidate
    outline = (project.config or {}).get("outline")
    if isinstance(outline, dict):
        from src.services.generation.planner import plan_from_outline

        try:
            for candidate in plan_from_outline(outline):
                if candidate.section_id == row.id:
                    return candidate
        except ValueError:
            pass
    return SectionPlan(
        section_id=row.id,
        chapter_number=row.chapter_number,
        section_number=row.section_number,
        title=row.title,
        chapter_title=row.chapter_title or "",
    )


async def _default_section_rewriter(
    project: Project, plan: SectionPlan, instruction: str
) -> SectionDraft:
    """실검색+실LLM으로 한 섹션 재작성. write 파이프라인의 검색기·생성기를 재사용한다.

    모델·페르소나·규칙을 작성 루프와 같은 방식으로 푼다. 전에는 전역 기본 모델로 쓰고
    페르소나·분량 목표를 통째로 잃었다 - 고급 모드를 골라도 재작성만 표준 모델이었고,
    그 절만 짧고 성격이 달라졌다(2026-08-11).
    """
    from src.services.sections.edit import regenerate_section
    from src.workflows.stages import (
        _analyst_catalog,
        _default_retriever_factory,
        _models_for,
        _rule_texts,
        _selected_rule_ids,
    )

    state = ProjectState(
        project_id=project.id,
        user_id=project.owner_id,
        topic=project.topic,
        preset=project.preset,
        options=project.config,
    )
    models = _models_for(state)
    retrieve = _default_retriever_factory(state)
    return await regenerate_section(
        section=plan,
        retrieve=retrieve,
        instruction=instruction,
        model=models["write"],
        plan_model=models["write_plan"],
        # 절 재작성도 그 런의 얼린 페르소나로 쓴다 - 공개 에이전트 주인이 그새
        # 고쳤다고 이미 쓴 절과 다른 목소리가 나오면 안 된다.
        analyst_catalog=await _analyst_catalog(project.owner_id, state.options),
        rules=await _rule_texts(project.owner_id, _selected_rule_ids(state)),
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
    current_user: Annotated[User, Depends(require_writer)],
) -> SectionContentResponse:
    """AI 재작성 — 프로젝트 인덱스에서 근거를 검색해 이 섹션만 다시 쓴다.

    instruction으로 방향을 지시할 수 있다(빈 값이면 근거 기반 단순 재작성).
    결과는 sections 테이블에 저장되고 갱신된 본문을 반환한다.
    """
    project = await _get_authorized_project(project_id, session, current_user)
    row = await _get_section(session, project.id, section_id)
    draft = await _section_rewriter(project, _plan_for_row(project, row), data.instruction)
    if draft.incomplete_reason or not draft.content.strip():
        # 작성 루프의 완결 게이트(check_complete)와 같은 기준 — max_tokens 컷·refusal
        # 토막을 저장하면 기존 본문만 잃는다. 실패 절 복구 경로가 여기라 더 엄격해야 한다.
        raise ValidationError(
            message="재작성 결과가 완결되지 않았습니다 - 잠시 후 다시 시도하세요",
            code="REWRITE_INCOMPLETE",
        )
    content = draft.content
    # 근거 추적 기록 — 재작성은 작성 루프 밖 경로라 그냥 두면 이 절만 추적이 반쪽이 된다
    # (실린 근거 수를 모르고, 마커→청크 대응도 복원할 수 없다).
    meta = {**(row.meta or {}), "pool_chunk_ids": [str(c) for c in draft.pool_chunk_ids]}
    meta["plan_failed"] = draft.split_fallback
    # 재작성이 새 근거로 목표를 채웠으면 '자료 부족' 배지를 내린다 — 안 갱신하면
    # 자료를 추가해 다시 써도 배지가 남는다(재업로드 워크플로우의 마감 신호).
    meta["volume_scaled"] = draft.volume_scaled
    if draft.pool_chunk_ids:
        meta["evidence_count"] = len(draft.pool_chunk_ids)
    try:
        # 재작성 결과도 전역 번호로 재매핑 — 문서의 나머지 절·출처장과 번호 체계 유지.
        from src.services.sections.renumber import (
            build_chunk_to_global,
            citation_chunk_map,
            renumber_content,
        )

        mapping = await build_chunk_to_global(project.id, set(draft.cited_chunk_ids))
        cited = list(draft.cited_chunk_ids)
        # 매핑은 재작성 전(절-로컬 번호) 본문 기준으로만 뜰 수 있다 — 순서 주의.
        marker_map = citation_chunk_map(draft.content, cited, mapping)
        if marker_map:
            meta["citation_chunks"] = {
                str(g): [str(c) for c in cids] for g, cids in sorted(marker_map.items())
            }
        content = renumber_content(draft.content, cited, mapping)
    except Exception:
        logger.warning("rewrite.renumber_failed", project_id=str(project.id), exc_info=True)
    row.content = content
    row.meta = meta
    row.source_ids = list(draft.cited_chunk_ids)
    row.status = "completed"
    row.qa_status = "passed"
    project.updated_at = clock_now()
    await session.flush()
    await session.refresh(row)
    return _section_content(
        row,
        await _section_citations(session, row, renumbered=_is_renumbered(project)),
    )


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
    current_user: Annotated[User, Depends(require_writer)],
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
    if has_chart_fence(data.block):
        # 차트 펜스는 사람이 표를 보고 바꾼 것이고, 그 수치는 이미 근거에 매여 게이트를
        # 통과한 값이다. 모델에게 다시 쓰게 하면 값·계열을 지어내 근거와 끊긴다 —
        # 고칠 게 있으면 표로 되돌린 뒤 고치고 다시 바꾸는 경로만 연다.
        raise ValidationError(
            message="그래프 블록은 AI 재작성 대상이 아닙니다 - 표로 되돌린 뒤 수정하세요",
            code="BLOCK_IS_CHART",
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
    return _section_content(
        row,
        await _section_citations(session, row, renumbered=_is_renumbered(project)),
    )
