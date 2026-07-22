from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import now
from src.db.models.quota_setting import QuotaSettings
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.services.quota_settings import invalidate_quota_setting_cache

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_quota_settings_cache() -> Iterator[None]:
    # 모듈 전역 캐시가 테스트 간에 새어나가지 않도록 매 테스트 전후로 비운다.
    invalidate_quota_setting_cache()
    yield
    invalidate_quota_setting_cache()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_setting(session: AsyncSession, key: str, value: str) -> QuotaSettings:
    row = QuotaSettings(key=key, value=value)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


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


def _this_month_start(today: datetime) -> datetime:
    return datetime(today.year, today.month, 1, tzinfo=today.tzinfo)


def _last_month_start(today: datetime) -> datetime:
    if today.month == 1:
        return datetime(today.year - 1, 12, 1, tzinfo=today.tzinfo)
    return datetime(today.year, today.month - 1, 1, tzinfo=today.tzinfo)


class TestAdminDashboardPeriod:
    async def test_default_period_is_this_month(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        await _add_usage(test_session, admin_user, Decimal("50"), created_at=today)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=_this_month_start(today) - timedelta(seconds=1),
        )

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        body = response.json()
        assert body["period"]["type"] == "this_month"
        assert body["kpis"]["total_cost_usd"] == 50.0

    async def test_last_month_excludes_this_month_includes_boundary(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        this_month_start = _this_month_start(today)
        last_month_start = _last_month_start(today)

        await _add_usage(test_session, admin_user, Decimal("999"), created_at=today)
        await _add_usage(test_session, admin_user, Decimal("30"), created_at=last_month_start)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=last_month_start - timedelta(seconds=1),
        )

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_month"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["period"]["type"] == "last_month"
        assert body["kpis"]["total_cost_usd"] == 30.0
        last_day = this_month_start.date() - timedelta(days=1)
        assert body["period"]["label"] == f"{last_month_start.date()} ~ {last_day}"

    async def test_last_7_days_boundary(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        midnight = datetime(today.year, today.month, today.day, tzinfo=today.tzinfo)
        window_start = midnight - timedelta(days=6)

        await _add_usage(test_session, admin_user, Decimal("20"), created_at=window_start)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=window_start - timedelta(seconds=1),
        )

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_7_days"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kpis"]["total_cost_usd"] == 20.0
        assert len(body["daily_costs"]) == 7

    async def test_last_30_days_daily_costs_length(
        self,
        test_client: AsyncClient,
        admin_token: str,
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_30_days"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        assert len(response.json()["daily_costs"]) == 30

    async def test_invalid_period_returns_422(
        self,
        test_client: AsyncClient,
        admin_token: str,
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "foo"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 422


class TestAdminDashboardQuotaSettingsOverride:
    async def test_cost_limit_usd_reflects_db_override(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "4321")

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        assert response.json()["kpis"]["cost_limit_usd"] == 4321.0

    async def test_user_usage_limit_reflects_db_override_for_role(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
        worker_user: User,
    ) -> None:
        await _seed_setting(test_session, "DEFAULT_LIMIT_WORKER_USD", "777")

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        rows = {row["user_id"]: row for row in response.json()["user_usage"]}
        assert rows[str(worker_user.id)]["limit_usd"] == 777.0

    async def test_dashboard_patch_then_reread_reflects_new_value_without_restart(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        first = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))
        assert first.status_code == 200
        assert first.json()["kpis"]["cost_limit_usd"] == 3000.0

        patch_response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "6000"},
            headers=_auth(super_admin_token),
        )
        assert patch_response.status_code == 200

        second = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))
        assert second.status_code == 200
        assert second.json()["kpis"]["cost_limit_usd"] == 6000.0
