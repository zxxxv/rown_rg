from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
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
from src.api.schemas.token_usage import (
    MyTokenUsageResponse,
    TokenUsageByModel,
    TokenUsageDailyPoint,
)
from src.api.schemas.user import UserRead, UserUpdate
from src.core.clock import now
from src.core.exceptions import AuthorizationError, NotFoundError
from src.core.types import Role
from src.db.models.limit_request import LimitRequest
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


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

    return MyTokenUsageResponse(
        period_start=period_start.date(),
        period_end=today.date(),
        total_input_tokens=sum(m.input_tokens for m in by_model),
        total_output_tokens=sum(m.output_tokens for m in by_model),
        total_cost_usd=sum((m.cost_usd for m in by_model), Decimal("0")),
        request_count=sum(m.request_count for m in by_model),
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


@router.get("", response_model=list[UserRead])
async def list_users(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(*ADMINS))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[User]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


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
    # 상위 역할 유저는 건드릴 수 없다(admin이 super_admin 비활성화·수정 차단).
    assert_can_manage_user(current_user, user)
    if data.role is not None:
        assert_can_assign_role(current_user, data.role)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    await session.flush()
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
