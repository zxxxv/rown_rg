from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import (
    ADMINS,
    assert_can_assign_role,
    assert_can_manage_user,
    require_role,
)
from src.api.schemas.admin import CreateLimitRequestInput, LimitRequestRead
from src.api.schemas.common import Page
from src.api.schemas.token_usage import (
    MyTokenUsageResponse,
    TokenUsageByModel,
    TokenUsageDailyPoint,
)
from src.api.schemas.user import UserListResponse, UserRead, UserUpdate
from src.core.clock import now
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.types import Role
from src.db.models.limit_request import LimitRequest
from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit
from src.services.quota_settings import get_role_default_limit_usd

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

_SORTABLE_COLUMNS: dict[str, ColumnElement] = {
    "created_at": User.created_at,
    "email": User.email,
    "name": User.name,
    "role": User.role,
}


@router.get("/me/token-usage", response_model=MyTokenUsageResponse)
async def my_token_usage(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> MyTokenUsageResponse:
    """현재 사용자의 이번 달 토큰 사용량(일별·모델별)을 집계해 반환한다."""
    today = now()
    period_start = datetime(today.year, today.month, 1, tzinfo=today.tzinfo)
    scope = (TokenUsage.user_id == current_user.id) & (TokenUsage.created_at >= period_start)

    day_col = func.date(TokenUsage.created_at)
    daily_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.coalesce(func.sum(TokenUsage.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(TokenUsage.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost_usd"),
            )
            .where(scope)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    model_rows = (
        await session.execute(
            select(
                TokenUsage.model.label("model"),
                func.coalesce(func.sum(TokenUsage.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(TokenUsage.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost_usd"),
                func.count().label("request_count"),
            )
            .where(scope)
            .group_by(TokenUsage.model)
            .order_by(func.coalesce(func.sum(TokenUsage.cost_usd), 0).desc())
        )
    ).all()

    daily = [
        TokenUsageDailyPoint(
            date=r.day,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
        )
        for r in daily_rows
    ]
    by_model = [
        TokenUsageByModel(
            model=r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
            request_count=r.request_count,
        )
        for r in model_rows
    ]

    # 한도는 게이트(clients/llm/quota_gate._fetch_usage)와 같은 규칙으로 푼다:
    # user_quotas 행이 있으면 그 값, 없으면 역할 기본값.
    custom_limit = (
        await session.execute(
            select(UserLimit.monthly_limit_usd).where(UserLimit.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    cost_limit = (
        custom_limit
        if custom_limit is not None
        else await get_role_default_limit_usd(session, current_user.role)
    )

    return MyTokenUsageResponse(
        period_start=period_start.date(),
        period_end=today.date(),
        total_input_tokens=sum(m.input_tokens for m in by_model),
        total_output_tokens=sum(m.output_tokens for m in by_model),
        total_cost_usd=sum((m.cost_usd for m in by_model), Decimal("0")),
        request_count=sum(m.request_count for m in by_model),
        cost_limit_usd=cost_limit,
        daily=daily,
        by_model=by_model,
    )


@router.post(
    "/me/quota-requests",  # 경로는 프론트 계약상 quota-requests 유지
    response_model=LimitRequestRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_my_limit_request(
    data: CreateLimitRequestInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> LimitRequestRead:
    """현재 사용자가 월 한도 증액을 신청한다(pending). 관리자가 승인/거절한다."""
    req = LimitRequest(
        user_id=current_user.id,
        amount_usd=Decimal(str(data.amount_usd)),
        reason=data.reason,
        status="pending",
        requested_at=now(),
    )
    session.add(req)
    await session.flush()
    return LimitRequestRead(
        id=req.id,
        user_id=req.user_id,
        user_name=current_user.name,
        amount_usd=float(req.amount_usd),
        reason=req.reason,
        requested_at=req.requested_at,
        status="pending",
    )


@router.get(
    "/me/quota-requests",  # 경로는 프론트 계약상 quota-requests 유지
    response_model=Page[LimitRequestRead],
)
async def list_my_quota_requests(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[LimitRequestRead]:
    """현재 사용자 본인의 증액 신청 목록을 신청일 최신순으로 페이지네이션하여 반환한다."""
    scope = LimitRequest.user_id == current_user.id

    total = (
        await session.execute(select(func.count()).select_from(LimitRequest).where(scope))
    ).scalar_one()

    rows = (
        (
            await session.execute(
                select(LimitRequest)
                .where(scope)
                .order_by(LimitRequest.requested_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    items = [
        LimitRequestRead(
            id=req.id,
            user_id=req.user_id,
            user_name=current_user.name,
            amount_usd=float(req.amount_usd),
            reason=req.reason,
            requested_at=req.requested_at,
            status=req.status,  # type: ignore[arg-type]
            decided_at=req.decided_at,
            decided_by=req.decided_by,
        )
        for req in rows
    ]
    return Page[LimitRequestRead](total=total, page=page, page_size=page_size, items=items)


@router.get("", response_model=UserListResponse)
async def list_users(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(*ADMINS))],
    q: Annotated[str | None, Query()] = None,
    role: Annotated[Role | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    sort_by: Annotated[Literal["created_at", "email", "name", "role"], Query()] = "created_at",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    """사용자 목록을 검색/필터/정렬 조건과 함께 페이지네이션하여 반환한다."""
    filters: list[ColumnElement[bool]] = []
    if q is not None:
        filters.append(or_(User.email.ilike(f"%{q}%"), User.name.ilike(f"%{q}%")))
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active == is_active)

    total = (
        await session.execute(select(func.count()).select_from(User).where(*filters))
    ).scalar_one()

    sort_col = _SORTABLE_COLUMNS[sort_by]
    order_col = sort_col.asc() if order == "asc" else sort_col.desc()

    rows = (
        (
            await session.execute(
                select(User).where(*filters).order_by(order_col).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return UserListResponse(items=list(rows), total=total)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    if current_user.id != user_id and current_user.role not in ADMINS:
        raise AuthorizationError(message="권한이 없습니다", code="FORBIDDEN")
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    return user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*ADMINS))],
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    # 자기 강등·자기 비활성화 차단 — 관리자가 스스로 권한을 잃고 잠기는 사고 방지.
    if user.id == current_user.id:
        if data.role is not None and data.role != user.role:
            raise ValidationError(
                message="자기 자신의 역할은 변경할 수 없습니다", code="CANNOT_CHANGE_OWN_ROLE"
            )
        if data.is_active is False:
            raise ValidationError(
                message="자기 자신을 비활성화할 수 없습니다", code="CANNOT_DEACTIVATE_SELF"
            )
    # 상위 역할 유저는 건드릴 수 없다(admin이 super_admin 비활성화·수정 차단).
    assert_can_manage_user(current_user, user)
    if data.role is not None:
        assert_can_assign_role(current_user, data.role)
        # 최고관리자는 한 명만 — 이미 다른 super_admin이 있으면 승격 거부.
        if data.role == Role.SUPER_ADMIN and user.role != Role.SUPER_ADMIN.value:
            existing = (
                await session.execute(
                    select(User.id)
                    .where(User.role == Role.SUPER_ADMIN.value, User.id != user_id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValidationError(
                    message="최고관리자는 한 명만 존재할 수 있습니다", code="SUPER_ADMIN_EXISTS"
                )
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    await session.flush()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    user.is_active = False


@router.delete("/{user_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_permanently(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> None:
    """계정을 영구 삭제한다(비활성화와 별개) — 되돌릴 수 없다.

    비활성화는 로그인만 막고 흔적을 남긴다. 오탈자 계정·퇴사자 정리처럼 흔적까지
    지워야 할 때가 있어 영구 삭제를 연다. 다만 보고서를 잃는 사고를 막으려고
    소유 프로젝트가 있으면 거부한다(FK도 RESTRICT라 DB가 한 번 더 막는다).

    남는 것: 토큰 사용량·라이브러리 자료·설정 변경 이력은 SET NULL로 보존된다
    (비용 집계와 회사 자료가 사용자 삭제로 사라지면 안 된다).
    사라지는 것: 로그인 세션·개인 프롬프트·개인 한도·한도 요청(CASCADE).
    """
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    if user.id == current_user.id:
        raise ValidationError(message="자기 계정은 삭제할 수 없습니다.", code="CANNOT_DELETE_SELF")
    if user.is_active:
        # 두 단계로 나눈다 — 실수로 한 번에 지우는 일을 막는다.
        raise ValidationError(
            message="먼저 계정을 비활성화한 뒤 삭제할 수 있습니다.",
            code="DEACTIVATE_FIRST",
        )
    if user.role == Role.SUPER_ADMIN.value:
        remaining = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == Role.SUPER_ADMIN.value, User.id != user.id)
            )
        ).scalar_one()
        if remaining == 0:
            raise ValidationError(
                message="마지막 최고관리자는 삭제할 수 없습니다.",
                code="LAST_SUPER_ADMIN",
            )
    n_projects = (
        await session.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == user.id)
        )
    ).scalar_one()
    if n_projects:
        raise ValidationError(
            message=(
                f"이 사용자가 소유한 보고서가 {n_projects}건 있습니다. "
                "보고서를 먼저 삭제하거나 비활성화 상태로 두세요."
            ),
            code="USER_HAS_PROJECTS",
        )
    await session.delete(user)
    logger.info(
        "admin.user_deleted",
        target_user_id=str(user.id),
        by=str(current_user.id),
    )
