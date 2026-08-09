from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.core.clock import now
from src.core.exceptions import CostLimitExceededError
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit
from src.services.quota_settings import get_role_default_limit_usd

logger = structlog.get_logger(__name__)

_WARNING_THRESHOLD_RATIO = Decimal("0.8")


async def enforce_cost_limit(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    today = now()
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    current_cost = (
        await session.execute(
            select(func.coalesce(func.sum(TokenUsage.cost_usd), 0)).where(
                TokenUsage.user_id == current_user.id,
                TokenUsage.created_at >= month_start,
            )
        )
    ).scalar_one()

    user_limit = await session.get(UserLimit, current_user.id)
    limit = (
        user_limit.monthly_limit_usd
        if user_limit
        else await get_role_default_limit_usd(session, current_user.role)
    )

    if current_cost >= limit:
        raise CostLimitExceededError(
            message=f"월 비용 한도를 초과했습니다 (${current_cost:.2f} / ${limit:.2f})",
            code="COST_LIMIT_EXCEEDED",
        )

    if limit > 0 and current_cost >= limit * _WARNING_THRESHOLD_RATIO:
        logger.warning(
            "cost_limit.warning_threshold",
            user_id=str(current_user.id),
            current_cost_usd=float(current_cost),
            limit_usd=float(limit),
        )

    return current_user
