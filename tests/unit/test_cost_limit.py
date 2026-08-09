from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from src.api.dependencies.cost_limit import enforce_cost_limit
from src.core.clock import now
from src.core.exceptions import CostLimitExceededError
from src.db.models.quota_setting import QuotaSettings
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.models.user_limit import UserLimit
from src.services.quota_settings import invalidate_quota_setting_cache


@pytest.fixture(autouse=True)
def _clear_quota_settings_cache() -> Iterator[None]:
    # 모듈 전역 캐시가 테스트 간에 새어나가지 않도록 매 테스트 전후로 비운다.
    invalidate_quota_setting_cache()
    yield
    invalidate_quota_setting_cache()


async def _add_usage(
    session: AsyncSession,
    user: User,
    cost_usd: Decimal,
    created_at: datetime | None = None,
) -> None:
    usage = TokenUsage(
        user_id=user.id,
        model="claude-opus-4-7",
        operation="test_op",
        input_tokens=100,
        output_tokens=100,
        cost_usd=cost_usd,
        mode="replay",
    )
    if created_at is not None:
        usage.created_at = created_at
    session.add(usage)
    await session.commit()


class TestEnforceCostLimit:
    async def test_under_limit_passes_without_warning(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        with capture_logs() as logs:
            result = await enforce_cost_limit(worker_user, test_session)

        assert result is worker_user
        assert not any(log["event"] == "cost_limit.warning_threshold" for log in logs)

    async def test_warns_at_80_percent_of_default_role_limit(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        # worker 기본 한도 200 USD → 170은 80% 임계값(160) 이상, 한도(200) 미만.
        await _add_usage(test_session, worker_user, Decimal("170"))

        with capture_logs() as logs:
            result = await enforce_cost_limit(worker_user, test_session)

        assert result is worker_user
        assert any(log["event"] == "cost_limit.warning_threshold" for log in logs)

    async def test_blocks_when_cost_reaches_limit(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        await _add_usage(test_session, worker_user, Decimal("100"))
        await _add_usage(test_session, worker_user, Decimal("100"))  # 합계 200 = 기본 한도

        with pytest.raises(CostLimitExceededError) as exc_info:
            await enforce_cost_limit(worker_user, test_session)

        assert exc_info.value.code == "COST_LIMIT_EXCEEDED"

    async def test_respects_per_user_limit_override(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        test_session.add(UserLimit(user_id=worker_user.id, monthly_limit_usd=Decimal("10")))
        await test_session.commit()
        # 기본 한도(200) 미만이지만 개인 override(10) 초과
        await _add_usage(test_session, worker_user, Decimal("15"))

        with pytest.raises(CostLimitExceededError):
            await enforce_cost_limit(worker_user, test_session)

    async def test_ignores_previous_month_cost(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        month_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = month_start - timedelta(seconds=1)
        await _add_usage(test_session, worker_user, Decimal("999"), created_at=last_month)

        result = await enforce_cost_limit(worker_user, test_session)

        assert result is worker_user

    async def test_uses_db_seeded_default_limit_when_present(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        # DEFAULT_LIMIT_WORKER_USD를 10으로 낮게 시딩하면, 상수 폴백(200)이 아니라
        # 이 DB 값이 우선해서 15 USD 사용만으로도 한도 초과가 발생해야 한다.
        test_session.add(QuotaSettings(key="DEFAULT_LIMIT_WORKER_USD", value="10"))
        await test_session.commit()
        await _add_usage(test_session, worker_user, Decimal("15"))

        with pytest.raises(CostLimitExceededError):
            await enforce_cost_limit(worker_user, test_session)
