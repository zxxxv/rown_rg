from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import assert_can_manage_user, require_role
from src.api.schemas.admin import (
    AdminDashboardData,
    AdminDashboardPeriod,
    AdminKPI,
    DailyCostPoint,
    DashboardPeriod,
    LimitDecisionInput,
    LimitRequestRead,
    SetUserLimitInput,
    UserLimitRead,
    UserUsageRow,
)
from src.api.schemas.common import Page
from src.core.clock import now
from src.core.config import settings
from src.core.exceptions import NotFoundError, ValidationError
from src.core.limit import default_limit_for
from src.db.models.limit_request import LimitRequest
from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit

router = APIRouter(prefix="/admin", tags=["admin"])

# DB의 ProjectStage 값 기준 '진행 중' 상태 (created/completed/archived 제외)
_ACTIVE_PROJECT_STATUSES = ("researching", "indexing", "writing", "reviewing")


def _resolve_period_range(period: DashboardPeriod, today: datetime) -> tuple[datetime, datetime]:
    """period를 (range_start, range_end)로 변환한다. start는 포함, end는 배타적 상한."""
    midnight = datetime(today.year, today.month, today.day, tzinfo=today.tzinfo)
    this_month_start = datetime(today.year, today.month, 1, tzinfo=today.tzinfo)

    if period == DashboardPeriod.THIS_MONTH:
        return this_month_start, today
    if period == DashboardPeriod.LAST_MONTH:
        if today.month == 1:
            last_month_start = datetime(today.year - 1, 12, 1, tzinfo=today.tzinfo)
        else:
            last_month_start = datetime(today.year, today.month - 1, 1, tzinfo=today.tzinfo)
        return last_month_start, this_month_start
    if period == DashboardPeriod.LAST_7_DAYS:
        return midnight - timedelta(days=6), today
    return midnight - timedelta(days=29), today


@router.get("/dashboard", response_model=AdminDashboardData)
async def get_admin_dashboard(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin", "admin"))],
    period: Annotated[DashboardPeriod, Query()] = DashboardPeriod.THIS_MONTH,
) -> AdminDashboardData:
    """관리자 대시보드 — KPI·일별 비용·사용자별 사용량·증액 요청을 한 번에 집계한다."""
    today = now()
    range_start, range_end = _resolve_period_range(period, today)

    # --- KPIs ---
    total_cost = (
        await session.execute(
            select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                TokenUsage.created_at >= range_start, TokenUsage.created_at < range_end
            )
        )
    ).scalar_one()
    active_users = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.is_active.is_(True),
                User.last_login_at >= range_start,
                User.last_login_at < range_end,
            )
        )
    ).scalar_one()
    active_projects = (
        await session.execute(
            select(func.count())
            .select_from(Project)
            .where(Project.status.in_(_ACTIVE_PROJECT_STATUSES))
        )
    ).scalar_one()
    completed_reports = (
        await session.execute(
            select(func.count())
            .select_from(Project)
            .where(
                Project.status == "completed",
                Project.updated_at >= range_start,
                Project.updated_at < range_end,
            )
        )
    ).scalar_one()

    kpis = AdminKPI(
        total_cost_usd=float(total_cost),
        cost_limit_usd=float(settings.org_monthly_cost_limit_usd),
        active_users=active_users,
        active_projects=active_projects,
        completed_reports=completed_reports,
    )

    # --- 일별 비용 (선택 기간 전체, 누락일은 0으로 채워 연속 시리즈로 반환) ---
    day_col = func.date(TokenUsage.created_at)
    daily_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost_usd"),
                func.count(func.distinct(TokenUsage.user_id)).label("users"),
            )
            .where(TokenUsage.created_at >= range_start, TokenUsage.created_at < range_end)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    by_day = {r.day: r for r in daily_rows}
    last_day = (range_end - timedelta(microseconds=1)).date()
    daily_costs: list[DailyCostPoint] = []
    d = range_start.date()
    while d <= last_day:
        row = by_day.get(d)
        daily_costs.append(
            DailyCostPoint(
                date=d,
                cost_usd=float(row.cost_usd) if row is not None else 0.0,
                users=int(row.users) if row is not None else 0,
            )
        )
        d += timedelta(days=1)

    # --- 사용자별 사용량 (활성 사용자 전체, 비용 높은 순) ---
    usage_subq = (
        select(
            TokenUsage.user_id.label("user_id"),
            func.coalesce(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0).label(
                "tokens"
            ),
            func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost"),
        )
        .where(TokenUsage.created_at >= range_start, TokenUsage.created_at < range_end)
        .group_by(TokenUsage.user_id)
        .subquery()
    )
    usage_rows = (
        await session.execute(
            select(
                User,
                func.coalesce(usage_subq.c.tokens, 0).label("tokens"),
                func.coalesce(usage_subq.c.cost, 0).label("cost"),
                UserLimit.monthly_limit_usd.label("limit"),
            )
            .outerjoin(usage_subq, User.id == usage_subq.c.user_id)
            .outerjoin(UserLimit, User.id == UserLimit.user_id)
            .where(User.is_active.is_(True))
            .order_by(func.coalesce(usage_subq.c.cost, 0).desc(), User.created_at.desc())
        )
    ).all()
    user_usage = [
        UserUsageRow(
            user_id=r.User.id,
            name=r.User.name,
            email=r.User.email,
            role=r.User.role,
            tokens_used=int(r.tokens),
            cost_usd=float(r.cost),
            limit_usd=float(r.limit if r.limit is not None else default_limit_for(r.User.role)),
            last_active=r.User.last_login_at or r.User.created_at,
        )
        for r in usage_rows
    ]

    # --- 증액 요청 (대기 중) ---
    qr_rows = (
        await session.execute(
            select(LimitRequest, User.name.label("user_name"))
            .join(User, LimitRequest.user_id == User.id)
            .where(LimitRequest.status == "pending")
            .order_by(LimitRequest.requested_at.desc())
        )
    ).all()
    quota_requests = [_to_limit_request_read(row.LimitRequest, row.user_name) for row in qr_rows]

    return AdminDashboardData(
        period=AdminDashboardPeriod(type=period, label=f"{range_start.date()} ~ {last_day}"),
        kpis=kpis,
        daily_costs=daily_costs,
        user_usage=user_usage,
        quota_requests=quota_requests,
    )


@router.patch("/users/{user_id}/quota", response_model=UserLimitRead)
async def set_user_limit(
    user_id: UUID,
    data: SetUserLimitInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> UserLimit:
    """사용자의 월 한도를 지정(없으면 생성, 있으면 갱신)한다."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    # 자기보다 상위 역할 사용자의 한도는 조정할 수 없다.
    assert_can_manage_user(current_user, user)
    limit = Decimal(str(data.monthly_limit_usd))
    user_limit = await session.get(UserLimit, user_id)
    if user_limit is None:
        user_limit = UserLimit(user_id=user_id, monthly_limit_usd=limit, updated_by=current_user.id)
        session.add(user_limit)
    else:
        user_limit.monthly_limit_usd = limit
        user_limit.updated_by = current_user.id
    await session.flush()
    await session.refresh(user_limit)
    return user_limit


@router.get("/quota-requests", response_model=Page[LimitRequestRead])
async def list_quota_requests(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin", "admin"))],
    status: Annotated[Literal["pending", "approved", "rejected"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[LimitRequestRead]:
    """전체 증액 요청을 조회한다(관리자 전용).

    status를 생략하면 전체 상태를 조회한다 — 필터 미지정이 특정 상태로
    좁혀지는 버그를 막기 위해 조건절 자체를 조건부로 추가한다.
    """
    filters = [LimitRequest.status == status] if status is not None else []

    total = (
        await session.execute(select(func.count()).select_from(LimitRequest).where(*filters))
    ).scalar_one()

    rows = (
        await session.execute(
            select(LimitRequest, User.name.label("user_name"))
            .join(User, LimitRequest.user_id == User.id)
            .where(*filters)
            .order_by(LimitRequest.requested_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items = [_to_limit_request_read(row.LimitRequest, row.user_name) for row in rows]
    return Page[LimitRequestRead](total=total, page=page, page_size=page_size, items=items)


@router.post("/quota-requests/{request_id}/decide", response_model=LimitRequestRead)
async def decide_limit_request(
    request_id: UUID,
    data: LimitDecisionInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> LimitRequestRead:
    """증액 요청을 승인/거절한다. 승인 시 해당 사용자 한도를 증액분만큼 올린다."""
    req = await session.get(LimitRequest, request_id)
    if req is None:
        raise NotFoundError(message="요청을 찾을 수 없습니다", code="QUOTA_REQUEST_NOT_FOUND")
    if req.status != "pending":
        raise ValidationError(
            message="이미 처리된 요청입니다", code="QUOTA_REQUEST_ALREADY_DECIDED"
        )

    target = await session.get(User, req.user_id)
    if target is None:
        raise NotFoundError(message="요청한 사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    # 자기보다 상위 역할 사용자의 한도는 조정할 수 없다.
    assert_can_manage_user(current_user, target)

    req.status = data.decision
    req.decided_by = current_user.id
    req.decided_at = now()

    if data.decision == "approved":
        user_limit = await session.get(UserLimit, req.user_id)
        if user_limit is None:
            base = default_limit_for(target.role)
            user_limit = UserLimit(
                user_id=req.user_id,
                monthly_limit_usd=base + req.amount_usd,
                updated_by=current_user.id,
            )
            session.add(user_limit)
        else:
            user_limit.monthly_limit_usd = user_limit.monthly_limit_usd + req.amount_usd
            user_limit.updated_by = current_user.id

    await session.flush()
    return _to_limit_request_read(req, target.name)


def _to_limit_request_read(req: LimitRequest, user_name: str) -> LimitRequestRead:
    return LimitRequestRead(
        id=req.id,
        user_id=req.user_id,
        user_name=user_name,
        amount_usd=float(req.amount_usd),
        reason=req.reason,
        requested_at=req.requested_at,
        status=req.status,  # type: ignore[arg-type]
        decided_at=req.decided_at,
        decided_by=req.decided_by,
    )
