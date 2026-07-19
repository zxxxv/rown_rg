from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.api.schemas.user import UserRole

# 비용 필드는 float로 노출한다 — 프론트 admin 타입(AdminKPI 등)이 number를 기대하고
# zod coerce를 거치지 않으므로, Decimal→문자열 직렬화를 피해 JSON number로 내려준다.


class AdminKPI(BaseModel):
    total_cost_usd: float
    cost_limit_usd: float
    active_users: int
    active_projects: int
    completed_reports: int


class DailyCostPoint(BaseModel):
    date: date
    cost_usd: float
    users: int


class UserUsageRow(BaseModel):
    user_id: UUID
    name: str
    email: str
    role: UserRole
    tokens_used: int
    cost_usd: float
    limit_usd: float
    last_active: datetime


class LimitRequestRead(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str
    amount_usd: float
    reason: str
    requested_at: datetime
    status: Literal["pending", "approved", "rejected"]


class DashboardPeriod(StrEnum):
    """대시보드 조회 기간 옵션."""

    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"


class AdminDashboardPeriod(BaseModel):
    type: DashboardPeriod
    label: str


class AdminDashboardData(BaseModel):
    period: AdminDashboardPeriod
    kpis: AdminKPI
    daily_costs: list[DailyCostPoint]
    user_usage: list[UserUsageRow]
    # 프론트 계약상 JSON 키는 quota_requests 유지(web dashboard가 data.quota_requests 사용).
    quota_requests: list[LimitRequestRead]


class LimitDecisionInput(BaseModel):
    decision: Literal["approved", "rejected"]


class SetUserLimitInput(BaseModel):
    monthly_limit_usd: float = Field(..., ge=0, description="새 월 한도(USD)")


class UserLimitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    monthly_limit_usd: float
    updated_at: datetime


class CreateLimitRequestInput(BaseModel):
    amount_usd: float = Field(..., gt=0, description="증액 요청 금액(USD)")
    reason: str = Field(..., min_length=1, max_length=2000, description="요청 사유")
