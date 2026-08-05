"""LLM 호출 전 한도 검사 게이트 — 당월 누적 비용 vs 사용자/조직 한도.

실제 provider 호출이 발생하는 경로(live/record)에서만 검사한다 — replay는 실비용이
없으므로 통과. 초과 시 QuotaExceededError(→ HTTP 429)로 호출 자체를 차단한다.

집계는 token_usage 합산(월초~현재, UTC). 기록이 best-effort 비동기(fire-and-forget)라
직전 몇 건이 아직 안 잡혔을 수 있다 — 한도는 정확 과금이 아니라 폭주 방지
가드레일이므로 이 오차는 허용한다(기술명세 §5.9 enforcement 항목).

호출 주체는 token_context의 user_id를 쓴다. user_id 없는 호출(시스템 작업)은
사용자 한도를 건너뛰고 조직 한도만 검사한다.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select

from src.clients.llm import token_tracker
from src.core.clock import now
from src.core.config import settings
from src.core.exceptions import QuotaExceededError
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit

logger = structlog.get_logger(__name__)


def check_limits(
    *,
    org_cost: Decimal,
    org_limit: Decimal,
    user_cost: Decimal | None = None,
    user_limit: Decimal | None = None,
) -> None:
    """누적 비용이 한도에 도달했으면 QuotaExceededError. (순수 판정 — 테스트 대상)

    '도달(>=)'에서 차단한다: 한도를 넘기는 마지막 호출까지는 허용되고 그다음부터
    막히는 사후 차단 모델(호출 1건의 비용은 완료 전엔 알 수 없음).
    """
    if org_cost >= org_limit:
        raise QuotaExceededError(
            f"조직 월 비용 한도 초과: ${org_cost:.2f} / ${org_limit:.2f}",
            code="ORG_QUOTA_EXCEEDED",
        )
    if user_cost is not None and user_limit is not None and user_cost >= user_limit:
        raise QuotaExceededError(
            f"사용자 월 비용 한도 초과: ${user_cost:.2f} / ${user_limit:.2f}",
            code="USER_QUOTA_EXCEEDED",
        )


async def _fetch_usage(
    user_id: UUID | None,
) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
    """(조직 당월 누적, 조직 한도, 사용자 당월 누적, 사용자 한도)를 조회.

    한도값은 quota_settings(관리자 편집·감사)를 단일 소스로 읽는다 — 조직 한도는
    ORG_MONTHLY_COST_LIMIT_USD, 역할 기본 한도는 get_role_default_limit_usd(둘 다
    quota_settings 미시딩 시 config/core.limit 폴백). 사용자 한도는 user_quotas 행 →
    없으면 역할 기본값. 사용자 행 자체가 없으면 사용자 검사를 건너뛴다(None).
    """
    from src.core.quota_settings import QuotaSettingKey
    from src.db.session import async_session_maker
    from src.services.quota_settings import get_quota_setting_int, get_role_default_limit_usd

    month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with async_session_maker() as session:
        org_limit = Decimal(
            await get_quota_setting_int(
                session,
                QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD,
                default=int(settings.org_monthly_cost_limit_usd),
            )
        )
        org_cost: Decimal = (
            await session.execute(
                select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                    TokenUsage.created_at >= month_start
                )
            )
        ).scalar_one()
        if user_id is None:
            return org_cost, org_limit, None, None

        row = (
            await session.execute(
                select(User.role, UserLimit.monthly_limit_usd)
                .outerjoin(UserLimit, UserLimit.user_id == User.id)
                .where(User.id == user_id)
            )
        ).first()
        if row is None:
            return org_cost, org_limit, None, None
        role, custom_limit = row
        user_limit = (
            custom_limit
            if custom_limit is not None
            else await get_role_default_limit_usd(session, role)
        )

        user_cost: Decimal = (
            await session.execute(
                select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                    TokenUsage.user_id == user_id,
                    TokenUsage.created_at >= month_start,
                )
            )
        ).scalar_one()
        return org_cost, org_limit, user_cost, user_limit


async def enforce() -> None:
    """실호출 직전 한도 게이트. BaseLLMAdapter가 live/record 경로에서 호출한다.

    QUOTA_ENFORCEMENT_ENABLED=false면 통과(표시·집계만 하는 종전 동작).
    """
    if not settings.quota_enforcement_enabled:
        return
    user_id, _project_id, _operation = token_tracker.get_context()
    org_cost, org_limit, user_cost, user_limit = await _fetch_usage(user_id)
    check_limits(
        org_cost=org_cost,
        org_limit=org_limit,
        user_cost=user_cost,
        user_limit=user_limit,
    )


async def check_user_quota(user_id: UUID) -> None:
    """실행 시작 전 사전 검사 — 한도 도달이면 QuotaExceededError(→429).

    LLM 게이트(enforce)와 같은 판정을 실행 진입점(POST /run·/decide)에서 미리 돌려,
    "실행이 시작됐다가 백그라운드에서 조용히 죽는" UX를 막는다(2026-08-05 실측 지적).
    """
    if not settings.quota_enforcement_enabled:
        return
    # _fetch_usage는 조직 한도까지 quota_settings에서 읽어 4-튜플로 돌려준다(enforce와 동일).
    org_cost, org_limit, user_cost, user_limit = await _fetch_usage(user_id)
    check_limits(
        org_cost=org_cost,
        org_limit=org_limit,
        user_cost=user_cost,
        user_limit=user_limit,
    )
