import ipaddress
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import ADMINS, assert_can_manage_user, require_role
from src.api.schemas.admin import (
    ActionResult,
    AdminDashboardData,
    AdminDashboardPeriod,
    AdminKPI,
    DailyCostPoint,
    DashboardPeriod,
    IpWhitelistCreateInput,
    IpWhitelistRead,
    IpWhitelistUpdateInput,
    LimitDecisionInput,
    LimitRequestRead,
    QuotaSettingRead,
    QuotaSettingsPatchBody,
    ResetPasswordInput,
    SetUserLimitInput,
    UserDailyPoint,
    UserDailySeries,
    UserLimitRead,
    UserSeriesMeta,
    UserUsageDetail,
    UserUsageDetailProject,
    UserUsageRow,
)
from src.api.schemas.common import Page
from src.core.clock import now
from src.core.config import settings
from src.core.exceptions import NotFoundError, ValidationError
from src.core.quota_settings import (
    QuotaSettingKey,
    parse_quota_setting_key,
    validate_quota_setting_value,
)
from src.core.types import Role
from src.db.models.ip_whitelist import IpWhitelist
from src.db.models.limit_request import LimitRequest
from src.db.models.project import Project
from src.db.models.quota_setting import QuotaSettings
from src.db.models.quota_setting_history import QuotaSettingsHistory
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit
from src.infrastructure.auth import lockout_handler, password_handler, refresh_token_handler
from src.services.quota_settings import (
    get_quota_setting_int,
    get_role_default_limit_usd,
    invalidate_quota_setting_cache,
)

router = APIRouter(prefix="/admin", tags=["admin"])

# DB의 ProjectStage 값 기준 '진행 중' 상태 (created/completed/archived 제외)
_ACTIVE_PROJECT_STATUSES = ("researching", "indexing", "writing", "reviewing")

# '현재 접속중' 판정 창 — 하트비트(last_seen_at)가 이 시간 내면 접속중으로 센다.
# 하트비트 갱신이 60초 스로틀이므로 창은 그보다 넉넉해야 한다.
_ONLINE_WINDOW = timedelta(minutes=5)


def _resolve_period_range(
    period: DashboardPeriod,
    today: datetime,
    start: date | None = None,
    end: date | None = None,
) -> tuple[datetime, datetime]:
    """period를 (range_start, range_end)로 변환한다. start는 포함, end는 배타적 상한.

    custom은 start/end(둘 다 필수, start<=end)로 구간을 직접 지정하며 end는 포함(그날 끝까지).
    """
    tz = today.tzinfo
    midnight = datetime(today.year, today.month, today.day, tzinfo=tz)
    this_month_start = datetime(today.year, today.month, 1, tzinfo=tz)

    if period == DashboardPeriod.CUSTOM:
        if start is None or end is None or start > end:
            raise ValidationError(
                message="custom 기간은 start·end(start ≤ end)가 필요합니다",
                code="INVALID_CUSTOM_RANGE",
            )
        range_start = datetime(start.year, start.month, start.day, tzinfo=tz)
        # end 포함 → 배타적 상한은 end 다음날 0시.
        range_end = datetime(end.year, end.month, end.day, tzinfo=tz) + timedelta(days=1)
        return range_start, range_end
    if period == DashboardPeriod.THIS_MONTH:
        return this_month_start, today
    if period == DashboardPeriod.LAST_MONTH:
        if today.month == 1:
            last_month_start = datetime(today.year - 1, 12, 1, tzinfo=tz)
        else:
            last_month_start = datetime(today.year, today.month - 1, 1, tzinfo=tz)
        return last_month_start, this_month_start
    if period == DashboardPeriod.LAST_7_DAYS:
        return midnight - timedelta(days=6), today
    return midnight - timedelta(days=29), today


@router.get("/dashboard", response_model=AdminDashboardData)
async def get_admin_dashboard(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(*ADMINS))],
    period: Annotated[DashboardPeriod, Query()] = DashboardPeriod.THIS_MONTH,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> AdminDashboardData:
    """관리자 대시보드 — KPI·일별 비용·사용자별 사용량·증액 요청을 한 번에 집계한다.

    기본 기간은 이번 달(월 한도와 같은 창, 월이 바뀌면 0부터 다시 누적).
    period=custom + start/end로 임의 구간을 조회한다.
    """
    today = now()
    range_start, range_end = _resolve_period_range(period, today, start, end)

    # --- KPIs ---
    total_cost = (
        await session.execute(
            select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                TokenUsage.created_at >= range_start, TokenUsage.created_at < range_end
            )
        )
    ).scalar_one()
    online_users = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.is_active.is_(True),
                User.last_seen_at >= today - _ONLINE_WINDOW,
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
                Project.completed_at >= range_start,
                Project.completed_at < range_end,
            )
        )
    ).scalar_one()

    org_cost_limit_usd = await get_quota_setting_int(
        session,
        QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD,
        default=int(settings.org_monthly_cost_limit_usd),
    )
    kpis = AdminKPI(
        total_cost_usd=float(total_cost),
        cost_limit_usd=float(org_cost_limit_usd),
        online_users=online_users,
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

    # --- 날짜별 사용자 기여 (상위 N명 + '기타') — 사용자별 누적 차트용 ---
    top_n = 6
    puser_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                TokenUsage.user_id.label("user_id"),
                func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("cost"),
            )
            .where(
                TokenUsage.created_at >= range_start,
                TokenUsage.created_at < range_end,
                TokenUsage.user_id.is_not(None),
            )
            .group_by(day_col, TokenUsage.user_id)
            .order_by(day_col)
        )
    ).all()
    user_totals: dict[UUID, float] = defaultdict(float)
    for r in puser_rows:
        user_totals[r.user_id] += float(r.cost)
    top_ids = [
        uid for uid, _ in sorted(user_totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    ]
    top_set = set(top_ids)
    top_names: dict[UUID, str] = (
        {
            uid: name
            for uid, name in (
                await session.execute(select(User.id, User.name).where(User.id.in_(top_ids)))
            ).all()
        }
        if top_ids
        else {}
    )
    series_meta = [UserSeriesMeta(key=str(uid), name=top_names.get(uid, "?")) for uid in top_ids]
    if any(uid not in top_set for uid in user_totals):
        series_meta.append(UserSeriesMeta(key="other", name="기타"))
    day_user_cost: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in puser_rows:
        skey = str(r.user_id) if r.user_id in top_set else "other"
        day_user_cost[r.day][skey] += float(r.cost)
    user_daily_points: list[UserDailyPoint] = []
    cur = range_start.date()
    while cur <= last_day:
        costs = {k: round(v, 6) for k, v in day_user_cost.get(cur, {}).items()}
        user_daily_points.append(UserDailyPoint(date=cur, costs=costs))
        cur += timedelta(days=1)
    user_daily = UserDailySeries(users=series_meta, points=user_daily_points)

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
    # UserLimit 오버라이드가 없는 사용자의 역할별 기본 한도는 역할당 한 번만 조회한다.
    roles_needing_default = {r.User.role for r in usage_rows if r.limit is None}
    role_default_limits = {
        role: await get_role_default_limit_usd(session, role) for role in roles_needing_default
    }

    user_usage = [
        UserUsageRow(
            user_id=r.User.id,
            name=r.User.name,
            email=r.User.email,
            role=r.User.role,
            tokens_used=int(r.tokens),
            cost_usd=float(r.cost),
            limit_usd=float(r.limit if r.limit is not None else role_default_limits[r.User.role]),
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
        user_daily=user_daily,
        user_usage=user_usage,
        quota_requests=quota_requests,
    )


@router.get("/users/{user_id}/usage", response_model=UserUsageDetail)
async def get_user_usage_detail(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(*ADMINS))],
    period: Annotated[DashboardPeriod, Query()] = DashboardPeriod.LAST_30_DAYS,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> UserUsageDetail:
    """사용자 클릭 상세 — 기간 내 지출·보고서 건수·프로젝트별 비용·일별 추이."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    today = now()
    range_start, range_end = _resolve_period_range(period, today, start, end)
    last_day = (range_end - timedelta(microseconds=1)).date()

    total_cost = (
        await session.execute(
            select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                TokenUsage.user_id == user_id,
                TokenUsage.created_at >= range_start,
                TokenUsage.created_at < range_end,
            )
        )
    ).scalar_one()

    user_limit = await session.get(UserLimit, user_id)
    limit_usd = (
        user_limit.monthly_limit_usd
        if user_limit is not None
        else await get_role_default_limit_usd(session, user.role)
    )

    # 소유 프로젝트 — 건수(전체/완료/진행) + 기간 내 프로젝트별 비용
    proj_rows = (
        await session.execute(
            select(
                Project.id, Project.title, Project.status, Project.completed_at, Project.created_at
            )
            .where(Project.owner_id == user_id)
            .order_by(Project.created_at.desc())
        )
    ).all()
    reports_total = len(proj_rows)
    reports_completed = sum(1 for p in proj_rows if p.status == "completed")
    reports_in_progress = sum(1 for p in proj_rows if p.status in _ACTIVE_PROJECT_STATUSES)

    cost_by_project: dict[UUID, float] = {
        pid: float(cost)
        for pid, cost in (
            await session.execute(
                select(TokenUsage.project_id, func.coalesce(func.sum(TokenUsage.cost_usd), 0))
                .where(
                    TokenUsage.user_id == user_id,
                    TokenUsage.created_at >= range_start,
                    TokenUsage.created_at < range_end,
                    TokenUsage.project_id.is_not(None),
                )
                .group_by(TokenUsage.project_id)
            )
        ).all()
    }
    projects = [
        UserUsageDetailProject(
            id=p.id,
            title=p.title,
            status=p.status,
            cost_usd=cost_by_project.get(p.id, 0.0),
            completed_at=p.completed_at,
            created_at=p.created_at,
        )
        for p in proj_rows[:30]
    ]

    # 이 사용자의 일별 비용 (연속 시리즈, 누락일 0)
    du_col = func.date(TokenUsage.created_at)
    daily_rows = (
        await session.execute(
            select(du_col.label("day"), func.coalesce(func.sum(TokenUsage.cost_usd), 0).label("c"))
            .where(
                TokenUsage.user_id == user_id,
                TokenUsage.created_at >= range_start,
                TokenUsage.created_at < range_end,
            )
            .group_by(du_col)
            .order_by(du_col)
        )
    ).all()
    by_day = {r.day: float(r.c) for r in daily_rows}
    daily_costs: list[DailyCostPoint] = []
    cur = range_start.date()
    while cur <= last_day:
        c = by_day.get(cur, 0.0)
        daily_costs.append(DailyCostPoint(date=cur, cost_usd=c, users=1 if c > 0 else 0))
        cur += timedelta(days=1)

    return UserUsageDetail(
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,  # type: ignore[arg-type]
        period=AdminDashboardPeriod(type=period, label=f"{range_start.date()} ~ {last_day}"),
        total_cost_usd=float(total_cost),
        limit_usd=float(limit_usd),
        reports_total=reports_total,
        reports_completed=reports_completed,
        reports_in_progress=reports_in_progress,
        projects=projects,
        daily_costs=daily_costs,
    )


@router.patch("/users/{user_id}/quota", response_model=UserLimitRead)
async def set_user_limit(
    user_id: UUID,
    data: SetUserLimitInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*ADMINS))],
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
    current_user: Annotated[User, Depends(require_role(*ADMINS))],
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
            base = await get_role_default_limit_usd(session, target.role)
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


@router.get("/quota-settings", response_model=list[QuotaSettingRead])
async def list_quota_settings(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> list[QuotaSettings]:
    """현재 DB에 저장된 전체 조직/사용자 한도(quota) 설정값을 조회한다."""
    rows = (
        (await session.execute(select(QuotaSettings).order_by(QuotaSettings.key))).scalars().all()
    )
    return list(rows)


@router.patch("/quota-settings", response_model=list[QuotaSettingRead])
async def update_quota_settings(
    body: QuotaSettingsPatchBody,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin"))],
) -> list[QuotaSettings]:
    """quota_settings를 배치로 수정한다(단일 트랜잭션, 실패 시 전체 롤백).

    수정 대상 row는 SELECT ... FOR UPDATE로 잠근 뒤 처리하고, 변경 건마다
    quota_settings_history에 감사 로그를 남긴다. 성공 시 해당 key들의
    인메모리 캐시를 즉시 무효화해 다음 조회부터 새 값이 반영되게 한다.
    """
    updates: dict[str, str] = (
        body if isinstance(body, dict) else {item.key: item.value for item in body}
    )

    if not updates:
        return []

    # key 화이트리스트 검증 — DB 조회 전에 걸러 존재하지 않는 잠금 대상 요청을 막는다.
    parsed_values: dict[str, int] = {}
    for key, value in updates.items():
        try:
            setting_key = parse_quota_setting_key(key)
        except ValueError as e:
            raise NotFoundError(message=str(e), code="QUOTA_SETTING_KEY_NOT_ALLOWED") from e
        try:
            parsed_values[key] = validate_quota_setting_value(setting_key, value)
        except ValueError as e:
            raise ValidationError(message=str(e), code="QUOTA_SETTING_VALUE_INVALID") from e

    # 동시성 제어 — 수정 대상 전체를 한 번에 잠근 뒤 처리한다.
    rows = (
        (
            await session.execute(
                select(QuotaSettings).where(QuotaSettings.key.in_(updates.keys())).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    rows_by_key = {row.key: row for row in rows}

    missing = set(updates.keys()) - set(rows_by_key.keys())
    if missing:
        raise NotFoundError(
            message=f"존재하지 않는 quota_settings key입니다: {sorted(missing)}",
            code="QUOTA_SETTING_NOT_FOUND",
        )

    changed_at = now()
    for key, parsed in parsed_values.items():
        row = rows_by_key[key]
        new_value = str(parsed)
        session.add(
            QuotaSettingsHistory(
                key=key,
                old_value=row.value,
                new_value=new_value,
                changed_at=changed_at,
                changed_by=current_user.id,
            )
        )
        row.value = new_value
        row.updated_at = changed_at
        row.updated_by = current_user.id

    await session.flush()
    for key in updates:
        invalidate_quota_setting_cache(key)

    return [rows_by_key[key] for key in updates]


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


# ─── 계정 운영 (잠금 해제 · 비밀번호 리셋) ──────────────────────────────


async def _get_managed_user(session: AsyncSession, user_id: UUID, actor: User) -> User:
    """대상 사용자 조회 + 위계 검증(자기보다 상위 역할은 관리 불가)."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    assert_can_manage_user(actor, user)
    return user


@router.post("/users/{user_id}/unlock", response_model=ActionResult)
async def unlock_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*ADMINS))],
) -> ActionResult:
    """로그인 실패 잠금을 수동 해제한다 (실패 카운트·잠금 시각 초기화).

    잠금은 30분 후 자동 해제되지만, 급한 사용자를 위해 관리자가 즉시 풀 수 있다.
    """
    user = await _get_managed_user(session, user_id, current_user)
    lockout_handler.reset_attempts(user)
    await session.flush()
    return ActionResult(detail="계정 잠금이 해제되었습니다")


@router.post("/users/{user_id}/reset-password", response_model=ActionResult)
async def reset_user_password(
    user_id: UUID,
    data: ResetPasswordInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*ADMINS))],
) -> ActionResult:
    """사용자 비밀번호를 관리자 권한으로 재설정한다 (분실 대응).

    - 동일 비밀번호 정책 적용, 잠금도 함께 해제, 기존 전 세션 폐기(강제 재로그인).
    - SSO 전용 사용자(password_hash NULL)에게 설정하면 비밀번호 로그인이 활성화된다.
    """
    user = await _get_managed_user(session, user_id, current_user)
    password_handler.validate_password_policy(data.new_password)
    user.password_hash = password_handler.hash_password(data.new_password)
    lockout_handler.reset_attempts(user)
    await refresh_token_handler.revoke_all_for_user(session, user.id)
    await session.flush()
    return ActionResult(detail="비밀번호가 재설정되었습니다 (기존 세션 전체 종료)")


# ─── IP 화이트리스트 관리 (super_admin 전용) ─────────────────────────────


def _normalize_cidr(value: str) -> str:
    """IP/CIDR 입력을 정규화한다. 단일 IP는 /32(/128)로. 형식 오류는 422."""
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError as exc:
        raise ValidationError(
            message=f"유효하지 않은 IP/CIDR: {value}", code="INVALID_CIDR"
        ) from exc


def _to_ip_read(row: IpWhitelist) -> IpWhitelistRead:
    # CIDR 컬럼은 드라이버가 네트워크 객체로 돌려줄 수 있어 str로 고정한다.
    return IpWhitelistRead(
        id=row.id,
        ip_cidr=str(row.ip_cidr),
        description=row.description,
        is_active=row.is_active,
        expires_at=row.expires_at,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/ip-whitelist", response_model=list[IpWhitelistRead])
async def list_ip_whitelist(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> list[IpWhitelistRead]:
    """IP 화이트리스트 전체 조회 (비활성·만료 포함, 최신 등록 순)."""
    rows = (
        await session.execute(select(IpWhitelist).order_by(IpWhitelist.created_at.desc()))
    ).scalars()
    return [_to_ip_read(r) for r in rows]


@router.post("/ip-whitelist", response_model=IpWhitelistRead, status_code=201)
async def create_ip_whitelist(
    data: IpWhitelistCreateInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> IpWhitelistRead:
    """허용 IP/CIDR 등록. expires_at을 주면 임시 허용(만료 시 미들웨어가 거부)."""
    cidr = _normalize_cidr(data.ip_cidr)
    if data.expires_at is not None and data.expires_at <= now():
        raise ValidationError(message="expires_at은 미래 시각이어야 합니다", code="EXPIRES_IN_PAST")
    duplicate = (
        await session.execute(
            select(IpWhitelist.id).where(
                IpWhitelist.ip_cidr == cidr, IpWhitelist.is_active.is_(True)
            )
        )
    ).first()
    if duplicate is not None:
        raise ValidationError(message=f"이미 등록된 CIDR: {cidr}", code="DUPLICATE_CIDR")

    row = IpWhitelist(
        ip_cidr=cidr,
        description=data.description,
        expires_at=data.expires_at,
        created_by=current_user.id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_ip_read(row)


@router.patch("/ip-whitelist/{entry_id}", response_model=IpWhitelistRead)
async def update_ip_whitelist(
    entry_id: UUID,
    data: IpWhitelistUpdateInput,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> IpWhitelistRead:
    """설명·활성 여부·만료 시각 부분 갱신 (명시한 필드만 반영)."""
    row = await session.get(IpWhitelist, entry_id)
    if row is None:
        raise NotFoundError(message="항목을 찾을 수 없습니다", code="IP_ENTRY_NOT_FOUND")
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(row, field, value)
    await session.flush()
    await session.refresh(row)
    return _to_ip_read(row)


@router.delete("/ip-whitelist/{entry_id}", response_model=ActionResult)
async def delete_ip_whitelist(
    entry_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(Role.SUPER_ADMIN))],
) -> ActionResult:
    """항목 삭제. 일시 차단이 목적이면 삭제 대신 is_active=false 갱신을 권장."""
    row = await session.get(IpWhitelist, entry_id)
    if row is None:
        raise NotFoundError(message="항목을 찾을 수 없습니다", code="IP_ENTRY_NOT_FOUND")
    await session.delete(row)
    await session.flush()
    return ActionResult(detail="삭제되었습니다")
