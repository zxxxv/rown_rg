"""한도 배분 가드 — 개인 한도가 조직 한도를 넘어 '있으나 마나'가 되던 자리.

quota_gate는 조직 한도를 **먼저** 본다(AND 조건). 그래서 역할 기본 한도가 조직
한도와 같거나 크면 개인 한도에 닿기 전에 조직 한도에서 막히고, 개인 한도는 아무
일도 하지 않는다 — 한 사람이 조직 예산 전체를 태워도 막을 수단이 없다.

2026-08-26 운영 실측이 정확히 그 상태였다: 조직 $500, 역할 기본값도 전부 $500,
활성 12명 → 배정 합계 $6,000(12배 초과 배정). 이 테스트들이 그 상태를 막는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.quota_setting import QuotaSettings
from src.db.models.user import User
from src.db.models.user_limit import UserLimit
from src.services.quota_settings import invalidate_quota_setting_cache

pytestmark = pytest.mark.integration

_ORG = "ORG_MONTHLY_COST_LIMIT_USD"
_WORKER = "DEFAULT_LIMIT_WORKER_USD"
_ADMIN = "DEFAULT_LIMIT_ADMIN_USD"
_SUPER = "DEFAULT_LIMIT_SUPER_ADMIN_USD"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_quota_settings_cache() -> Iterator[None]:
    invalidate_quota_setting_cache()
    yield
    invalidate_quota_setting_cache()


async def _seed(session: AsyncSession, **values: int) -> None:
    for key, value in values.items():
        session.add(QuotaSettings(key=key, value=str(value)))
    await session.commit()


class TestRoleDefaultCannotExceedOrgLimit:
    async def test_raising_role_default_above_org_is_rejected(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        await _seed(test_session, **{_ORG: 500, _WORKER: 100, _ADMIN: 100, _SUPER: 100})

        resp = await test_client.patch(
            "/api/v1/admin/quota-settings",
            headers=_auth(super_admin_token),
            json={_WORKER: "600"},
        )

        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "ROLE_LIMIT_EXCEEDS_ORG"

    async def test_lowering_org_below_existing_role_default_is_rejected(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        """이 배치가 안 건드린 역할도 본다 — 조직 한도만 내려도 역전이 생긴다."""
        await _seed(test_session, **{_ORG: 500, _WORKER: 400, _ADMIN: 100, _SUPER: 100})

        resp = await test_client.patch(
            "/api/v1/admin/quota-settings",
            headers=_auth(super_admin_token),
            json={_ORG: "300"},
        )

        assert resp.status_code == 422, resp.text
        assert "DEFAULT_LIMIT_WORKER_USD" in resp.json()["error"]["message"]

    async def test_same_batch_can_raise_org_and_role_together(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        """한 배치에 같이 오면 반영 후 값으로 판정한다 — 순서 때문에 막히면 안 된다."""
        await _seed(test_session, **{_ORG: 500, _WORKER: 100, _ADMIN: 100, _SUPER: 100})

        resp = await test_client.patch(
            "/api/v1/admin/quota-settings",
            headers=_auth(super_admin_token),
            json={_ORG: "2000", _WORKER: "1500"},
        )

        assert resp.status_code == 200, resp.text

    async def test_equal_to_org_limit_is_allowed(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        """같은 값까지는 허용 — 1인 조직처럼 개인=조직이 맞는 구성도 있다."""
        await _seed(test_session, **{_ORG: 500, _WORKER: 100, _ADMIN: 100, _SUPER: 100})

        resp = await test_client.patch(
            "/api/v1/admin/quota-settings",
            headers=_auth(super_admin_token),
            json={_WORKER: "500"},
        )

        assert resp.status_code == 200, resp.text


class TestAllocatedTotalIsVisible:
    async def test_dashboard_reports_allocated_sum(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_user: User,
        worker_user: User,
        admin_user: User,
        super_admin_token: str,
    ) -> None:
        """배정 합계 = 전용 한도가 있으면 그 값, 없으면 역할 기본값.

        조직 한도와 나란히 내려가야 초과 배정을 화면이 알린다.
        """
        await _seed(test_session, **{_ORG: 500, _WORKER: 100, _ADMIN: 200, _SUPER: 300})
        # worker에게만 전용 한도 — 역할 기본값(100) 대신 이 값이 세어져야 한다.
        test_session.add(UserLimit(user_id=worker_user.id, monthly_limit_usd=Decimal("250")))
        await test_session.commit()

        resp = await test_client.get("/api/v1/admin/dashboard", headers=_auth(super_admin_token))

        assert resp.status_code == 200, resp.text
        kpis = resp.json()["kpis"]
        # super_admin 300 + admin 200 + worker 250(전용) = 750 > 조직 500 → 초과 배정
        assert kpis["allocated_limit_usd"] == 750.0
        assert kpis["cost_limit_usd"] == 500.0
        assert kpis["allocated_limit_usd"] > kpis["cost_limit_usd"]
