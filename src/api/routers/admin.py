import ipaddress
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
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
    IpWhitelistCreateInput,
    IpWhitelistRead,
    IpWhitelistUpdateInput,
    LimitDecisionInput,
    LimitRequestRead,
    ResetPasswordInput,
    SetUserLimitInput,
    UserLimitRead,
    UserUsageRow,
)
from src.core import app_settings
from src.core.clock import now
from src.core.exceptions import NotFoundError, ValidationError
from src.core.limit import default_limit_for
from src.core.types import Role
from src.db.models.ip_whitelist import IpWhitelist
from src.db.models.limit_request import LimitRequest
from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit
from src.infrastructure.auth import lockout_handler, password_handler, refresh_token_handler

router = APIRouter(prefix="/admin", tags=["admin"])

# DB의 ProjectStage 값 기준 '진행 중' 상태 (created/completed/archived 제외)
_ACTIVE_PROJECT_STATUSES = ("researching", "indexing", "writing", "reviewing")
# 일별 비용 차트 윈도우(일)
_DAILY_WINDOW_DAYS = 30


@router.get("/dashboard", response_model=AdminDashboardData)
async def get_admin_dashboard(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(*ADMINS))],
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
        cost_limit_usd=float(app_settings.get_decimal("org_monthly_cost_limit_usd")),
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
        period=AdminDashboardPeriod(label=today.strftime("%Y-%m")),
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
