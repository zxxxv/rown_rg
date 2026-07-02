from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import assert_can_manage_user, require_role
from src.api.schemas.admin import (
    AdminDashboardData,
    AdminDashboardPeriod,
    AdminKPI,
    DailyCostPoint,
    QuotaDecisionInput,
    QuotaRequestRead,
    SetUserQuotaInput,
    UserQuotaRead,
    UserUsageRow,
)
from src.core.clock import now
from src.core.config import settings
from src.core.exceptions import NotFoundError, ValidationError
from src.core.quota import default_quota_for
from src.db.models.project import Project
from src.db.models.quota_request import QuotaRequest
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_quota import UserQuota

router = APIRouter(prefix="/admin", tags=["admin"])

# DB의 ProjectStage 값 기준 '진행 중' 상태 (created/completed/archived 제외)
_ACTIVE_PROJECT_STATUSES = ("researching", "indexing", "writing", "reviewing")
# 일별 비용 차트 윈도우(일)
_DAILY_WINDOW_DAYS = 30


@router.get("/dashboard", response_model=AdminDashboardData)
async def get_admin_dashboard(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> AdminDashboardData:
    """관리자 대시보드 — KPI·일별 비용·사용자별 사용량·증액 요청을 한 번에 집계한다."""
    today = now()
    month_start = datetime(today.year, today.month, 1, tzinfo=today.tzinfo)

    # --- KPIs ---
    total_cost = (
        await session.execute(
            select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                TokenUsage.created_at >= month_start
            )
        )
    ).scalar_one()
    active_users = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.is_active.is_(True), User.last_login_at >= month_start)
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
            .where(Project.status == "completed", Project.updated_at >= month_start)
        )
    ).scalar_one()

    kpis = AdminKPI(
        total_cost_usd=float(total_cost),
        cost_limit_usd=float(settings.org_monthly_cost_limit_usd),
        active_users=active_users,
        active_projects=active_projects,
        completed_reports=completed_reports,
    )

    # --- 일별 비용 (최근 30일, 누락일은 0으로 채워 연속 시리즈로 반환) ---
    today_midnight = datetime(today.year, today.month, today.day, tzinfo=today.tzinfo)
    window_start = today_midnight - timedelta(days=_DAILY_WINDOW_DAYS - 1)
    day_col = func.date(TokenUsage.created_at)
    daily_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost_usd"),
                func.count(func.distinct(TokenUsage.user_id)).label("users"),
            )
            .where(TokenUsage.created_at >= window_start)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    by_day = {r.day: r for r in daily_rows}
    daily_costs: list[DailyCostPoint] = []
    for i in range(_DAILY_WINDOW_DAYS):
        d = (window_start + timedelta(days=i)).date()
        row = by_day.get(d)
        daily_costs.append(
            DailyCostPoint(
                date=d,
                cost_usd=float(row.cost_usd) if row is not None else 0.0,
                users=int(row.users) if row is not None else 0,
            )
        )

    # --- 사용자별 사용량 (활성 사용자 전체, 비용 높은 순) ---
    usage_subq = (
        select(
            TokenUsage.user_id.label("user_id"),
            func.coalesce(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0).label(
                "tokens"
            ),
            func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost"),
        )
        .where(TokenUsage.created_at >= month_start)
        .group_by(TokenUsage.user_id)
        .subquery()
    )
    usage_rows = (
        await session.execute(
            select(
                User,
                func.coalesce(usage_subq.c.tokens, 0).label("tokens"),
                func.coalesce(usage_subq.c.cost, 0).label("cost"),
                UserQuota.monthly_limit_usd.label("limit"),
            )
            .outerjoin(usage_subq, User.id == usage_subq.c.user_id)
            .outerjoin(UserQuota, User.id == UserQuota.user_id)
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
            limit_usd=float(r.limit if r.limit is not None else default_quota_for(r.User.role)),
            last_active=r.User.last_login_at or r.User.created_at,
        )
        for r in usage_rows
    ]

    # --- 증액 요청 (대기 중) ---
    qr_rows = (
        await session.execute(
            select(QuotaRequest, User.name.label("user_name"))
            .join(User, QuotaRequest.user_id == User.id)
            .where(QuotaRequest.status == "pending")
            .order_by(QuotaRequest.requested_at.desc())
        )
    ).all()
    quota_requests = [_to_quota_request_read(row.QuotaRequest, row.user_name) for row in qr_rows]

    return AdminDashboardData(
        period=AdminDashboardPeriod(label=today.strftime("%Y-%m")),
        kpis=kpis,
        daily_costs=daily_costs,
        user_usage=user_usage,
        quota_requests=quota_requests,
    )


@router.patch("/users/{user_id}/quota", response_model=UserQuotaRead)
async def set_user_quota(
    user_id: UUID,
    data: SetUserQuotaInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> UserQuota:
    """사용자의 월 한도를 지정(없으면 생성, 있으면 갱신)한다."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    # 자기보다 상위 역할 사용자의 한도는 조정할 수 없다.
    assert_can_manage_user(current_user, user)
    limit = Decimal(str(data.monthly_limit_usd))
    quota = await session.get(UserQuota, user_id)
    if quota is None:
        quota = UserQuota(user_id=user_id, monthly_limit_usd=limit, updated_by=current_user.id)
        session.add(quota)
    else:
        quota.monthly_limit_usd = limit
        quota.updated_by = current_user.id
    await session.flush()
    await session.refresh(quota)
    return quota


@router.post("/quota-requests/{request_id}/decide", response_model=QuotaRequestRead)
async def decide_quota_request(
    request_id: UUID,
    data: QuotaDecisionInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> QuotaRequestRead:
    """증액 요청을 승인/거절한다. 승인 시 해당 사용자 한도를 증액분만큼 올린다."""
    req = await session.get(QuotaRequest, request_id)
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
        quota = await session.get(UserQuota, req.user_id)
        if quota is None:
            base = default_quota_for(target.role)
            quota = UserQuota(
                user_id=req.user_id,
                monthly_limit_usd=base + req.amount_usd,
                updated_by=current_user.id,
            )
            session.add(quota)
        else:
            quota.monthly_limit_usd = quota.monthly_limit_usd + req.amount_usd
            quota.updated_by = current_user.id

    await session.flush()
    return _to_quota_request_read(req, target.name)


def _to_quota_request_read(req: QuotaRequest, user_name: str) -> QuotaRequestRead:
    return QuotaRequestRead(
        id=req.id,
        user_id=req.user_id,
        user_name=user_name,
        amount_usd=float(req.amount_usd),
        reason=req.reason,
        requested_at=req.requested_at,
        status=req.status,  # type: ignore[arg-type]
    )
