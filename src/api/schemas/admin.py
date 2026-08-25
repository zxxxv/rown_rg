from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.api.schemas.user import UserRole

# 비용 필드는 float로 노출한다 — 프론트 admin 타입(AdminKPI 등)이 number를 기대하고
# zod coerce를 거치지 않으므로, Decimal→문자열 직렬화를 피해 JSON number로 내려준다.


class AdminKPI(BaseModel):
    total_cost_usd: float
    cost_limit_usd: float
    # 활성 사용자에게 배정된 개인 한도의 합(전용 한도 있으면 그 값, 없으면 역할 기본값).
    # 조직 한도(cost_limit_usd)를 넘으면 초과 배정 — 실제 지출은 조직 한도에서 먼저
    # 막히므로 "늘려줬다"는 약속이 실효가 없다는 뜻이다. 배분과 실링을 나란히 보여준다.
    allocated_limit_usd: float
    # 현재 접속중 — last_seen_at이 최근 5분 이내인 사용자 수 (기간 무관)
    online_users: int
    # 기간 활성 — 조회 기간 내 1회 이상 로그인한 사용자 수
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
    # pending 상태에서는 아직 처리되지 않았으므로 둘 다 None — 필수로 Optional 처리.
    decided_at: datetime | None = None
    decided_by: UUID | None = None


class DashboardPeriod(StrEnum):
    """대시보드 조회 기간 옵션. custom은 start/end 쿼리로 구간을 직접 지정한다."""

    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    CUSTOM = "custom"


class AdminDashboardPeriod(BaseModel):
    type: DashboardPeriod
    label: str


class UserSeriesMeta(BaseModel):
    """사용자별 누적 차트의 시리즈 1개(상위 N명 + 'other' 기타 버킷)."""

    key: str  # user_id(문자열) 또는 "other"
    name: str  # 표시 이름 또는 "기타"


class UserDailyPoint(BaseModel):
    date: date
    # key(user_id 또는 "other") → 그날 비용. 값 없는 시리즈는 프론트에서 0 처리.
    costs: dict[str, float]


class UserDailySeries(BaseModel):
    """날짜별 사용자 기여 — 상위 N명 시리즈 + 나머지는 'other'로 합산."""

    users: list[UserSeriesMeta]
    points: list[UserDailyPoint]


class AdminDashboardData(BaseModel):
    period: AdminDashboardPeriod
    kpis: AdminKPI
    daily_costs: list[DailyCostPoint]
    # 날짜별 '누가 얼마나' — 사용자별 누적 차트용(상위 N명 + 기타).
    user_daily: UserDailySeries
    user_usage: list[UserUsageRow]
    # 프론트 계약상 JSON 키는 quota_requests 유지(web dashboard가 data.quota_requests 사용).
    quota_requests: list[LimitRequestRead]


class UserUsageDetailProject(BaseModel):
    id: UUID
    title: str
    status: str
    cost_usd: float  # 이 기간 이 프로젝트에 귀속된 비용
    completed_at: datetime | None = None
    created_at: datetime


class UserUsageDetail(BaseModel):
    """사용자 클릭 상세 — 기간 내 지출·보고서 건수·프로젝트별 비용·일별 추이."""

    user_id: UUID
    name: str
    email: str
    role: UserRole
    period: AdminDashboardPeriod
    total_cost_usd: float
    limit_usd: float
    reports_total: int
    reports_completed: int
    reports_in_progress: int
    projects: list[UserUsageDetailProject]
    daily_costs: list[DailyCostPoint]


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


class ActionResult(BaseModel):
    """단순 관리 액션(잠금 해제·비번 리셋·삭제)의 결과 메시지."""

    detail: str


class ResetPasswordInput(BaseModel):
    # 길이·복잡도 정책 검증은 password_handler.validate_password_policy가 담당.
    new_password: str = Field(..., min_length=1, max_length=128)


def _require_aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("expires_at는 timezone-aware(UTC) 값이어야 합니다")
    return v


class IpWhitelistCreateInput(BaseModel):
    ip_cidr: str = Field(..., description="허용 IP 또는 CIDR (예: 1.2.3.4, 10.0.0.0/24)")
    description: str | None = Field(None, max_length=255)
    expires_at: datetime | None = Field(None, description="임시 허용 만료 시각. 없으면 영구")

    _aware = field_validator("expires_at")(_require_aware)


class IpWhitelistUpdateInput(BaseModel):
    """부분 갱신 — 명시한 필드만 반영한다(exclude_unset)."""

    description: str | None = Field(None, max_length=255)
    is_active: bool | None = None
    expires_at: datetime | None = None

    _aware = field_validator("expires_at")(_require_aware)


class IpWhitelistRead(BaseModel):
    id: UUID
    ip_cidr: str
    description: str | None
    is_active: bool
    expires_at: datetime | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class QuotaSettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str
    updated_at: datetime
    updated_by: UUID | None = None


class QuotaSettingUpdateItem(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: str = Field(..., min_length=1, max_length=255)


QuotaSettingsPatchBody = dict[str, str] | list[QuotaSettingUpdateItem]
