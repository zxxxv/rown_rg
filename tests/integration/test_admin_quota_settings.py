from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.quota_setting import QuotaSettings
from src.db.models.quota_setting_history import QuotaSettingsHistory
from src.db.models.user import User
from src.services.quota_settings import get_quota_setting, invalidate_quota_setting_cache

pytestmark = pytest.mark.integration


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_setting(session: AsyncSession, key: str, value: str) -> QuotaSettings:
    row = QuotaSettings(key=key, value=value)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@pytest.fixture(autouse=True)
def _clear_quota_settings_cache() -> Iterator[None]:
    # 모듈 전역 캐시가 테스트 간에 새어나가지 않도록 매 테스트 전후로 비운다.
    invalidate_quota_setting_cache()
    yield
    invalidate_quota_setting_cache()


class TestListQuotaSettings:
    async def test_admin_can_list(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")
        await _seed_setting(test_session, "DEFAULT_LIMIT_WORKER_USD", "200")

        response = await test_client.get("/api/v1/admin/quota-settings", headers=_auth(admin_token))

        assert response.status_code == 200
        body = response.json()
        keys = {row["key"] for row in body}
        assert keys == {"ORG_MONTHLY_COST_LIMIT_USD", "DEFAULT_LIMIT_WORKER_USD"}
        assert all(row["updated_by"] is None for row in body)

    async def test_worker_forbidden(
        self,
        test_client: AsyncClient,
        worker_token: str,
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/quota-settings", headers=_auth(worker_token)
        )
        assert response.status_code == 403

    async def test_viewer_forbidden(
        self,
        test_client: AsyncClient,
        viewer_token: str,
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/quota-settings", headers=_auth(viewer_token)
        )
        assert response.status_code == 403


class TestUpdateQuotaSettings:
    async def test_admin_can_update(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
    ) -> None:
        """admin도 조직 한도를 고친다(2026-08-26 결정 — 종전 super_admin 전용).

        결재선을 줄이는 대신, 개인 한도를 실제 가드로 돌려놓은
        `_assert_role_defaults_within_org`와 감사 이력이 안전망이다.
        """
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "4000"},
            headers=_auth(admin_token),
        )
        assert response.status_code == 200, response.text
        assert response.json()[0]["value"] == "4000"

    async def test_worker_still_forbidden(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        worker_token: str,
    ) -> None:
        """넓힌 것은 admin까지다 — 그 아래는 그대로 막힌다."""
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "4000"},
            headers=_auth(worker_token),
        )
        assert response.status_code == 403

    async def test_super_admin_dict_body_updates_value_and_records_history(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_user: User,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "4000"},
            headers=_auth(super_admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body[0]["key"] == "ORG_MONTHLY_COST_LIMIT_USD"
        assert body[0]["value"] == "4000"
        assert body[0]["updated_by"] == str(super_admin_user.id)

        row = (
            await test_session.execute(
                select(QuotaSettings).where(QuotaSettings.key == "ORG_MONTHLY_COST_LIMIT_USD")
            )
        ).scalar_one()
        assert row.value == "4000"
        assert row.updated_by == super_admin_user.id

        history = (
            await test_session.execute(
                select(QuotaSettingsHistory).where(
                    QuotaSettingsHistory.key == "ORG_MONTHLY_COST_LIMIT_USD"
                )
            )
        ).scalar_one()
        assert history.old_value == "3000"
        assert history.new_value == "4000"
        assert history.changed_by == super_admin_user.id

    async def test_super_admin_list_body_form(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "DEFAULT_LIMIT_WORKER_USD", "200")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json=[{"key": "DEFAULT_LIMIT_WORKER_USD", "value": "250"}],
            headers=_auth(super_admin_token),
        )

        assert response.status_code == 200
        assert response.json()[0]["value"] == "250"

    async def test_unknown_key_returns_404_and_rolls_back(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "4000", "NOT_A_REAL_KEY": "1"},
            headers=_auth(super_admin_token),
        )

        assert response.status_code == 404
        row = (
            await test_session.execute(
                select(QuotaSettings).where(QuotaSettings.key == "ORG_MONTHLY_COST_LIMIT_USD")
            )
        ).scalar_one()
        assert row.value == "3000"  # 배치 전체가 롤백되어 유효한 key도 반영되지 않아야 한다.

    async def test_non_integer_value_returns_422_and_rolls_back(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "not-a-number"},
            headers=_auth(super_admin_token),
        )

        assert response.status_code == 422
        row = (
            await test_session.execute(
                select(QuotaSettings).where(QuotaSettings.key == "ORG_MONTHLY_COST_LIMIT_USD")
            )
        ).scalar_one()
        assert row.value == "3000"

    async def test_out_of_range_value_returns_422(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "DEFAULT_LIMIT_WORKER_USD", "200")

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"DEFAULT_LIMIT_WORKER_USD": "0"},
            headers=_auth(super_admin_token),
        )

        assert response.status_code == 422

    async def test_cache_invalidated_after_update(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        super_admin_token: str,
    ) -> None:
        from src.core.quota_settings import QuotaSettingKey

        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")
        assert (
            await get_quota_setting(test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD)
            == "3000"
        )

        response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "5000"},
            headers=_auth(super_admin_token),
        )
        assert response.status_code == 200

        assert (
            await get_quota_setting(test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD)
            == "5000"
        )
