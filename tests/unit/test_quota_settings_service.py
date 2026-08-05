from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.core.quota_settings import QuotaSettingKey
from src.db.models.quota_setting import QuotaSettings
from src.services.quota_settings import (
    get_quota_setting,
    get_quota_setting_int,
    get_role_default_limit_usd,
    invalidate_quota_setting_cache,
)


@pytest.fixture(autouse=True)
def _clear_quota_settings_cache() -> Iterator[None]:
    # 모듈 전역 캐시가 테스트 간에 새어나가지 않도록 매 테스트 전후로 비운다.
    invalidate_quota_setting_cache()
    yield
    invalidate_quota_setting_cache()


async def _seed_setting(session: AsyncSession, key: str, value: str) -> QuotaSettings:
    row = QuotaSettings(key=key, value=value)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


class TestGetQuotaSetting:
    async def test_raises_without_default_when_row_missing(
        self, test_session: AsyncSession
    ) -> None:
        with pytest.raises(NotFoundError):
            await get_quota_setting(test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD)

    async def test_returns_default_when_row_missing(self, test_session: AsyncSession) -> None:
        value = await get_quota_setting(
            test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD, default="1234"
        )
        assert value == "1234"

    async def test_fallback_is_not_cached_and_self_heals(self, test_session: AsyncSession) -> None:
        # 첫 호출은 row가 없어 fallback을 반환한다.
        assert (
            await get_quota_setting(
                test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD, default="1234"
            )
            == "1234"
        )

        # 이후 row가 생기면, fallback이 캐시에 남아있지 않으므로
        # 다음 호출은 실제 DB 값을 반환해야 한다.
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "5000")
        assert (
            await get_quota_setting(
                test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD, default="1234"
            )
            == "5000"
        )

    async def test_returns_db_value_when_row_present(self, test_session: AsyncSession) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "9999")
        value = await get_quota_setting(
            test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD, default="1234"
        )
        assert value == "9999"

    async def test_falls_back_when_table_itself_is_missing(
        self, test_session: AsyncSession
    ) -> None:
        # 마이그레이션 0019가 아직 실행되지 않은 상태를 시뮬레이션한다 — row가 아니라
        # 테이블 자체가 없는 경우. SAVEPOINT로 감싸져 있으므로 세션 자체는 계속
        # 사용 가능해야 한다(바깥 트랜잭션이 오염되지 않음).
        await test_session.execute(text("DROP TABLE quota_settings"))

        value = await get_quota_setting(
            test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD, default="1234"
        )
        assert value == "1234"

        # 세션이 여전히 정상 동작하는지 확인 — 실패한 SELECT가 바깥 트랜잭션까지
        # 롤백시켰다면 이 쿼리도 실패한다.
        result = await test_session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


class TestGetQuotaSettingInt:
    async def test_returns_default_int_when_row_missing(self, test_session: AsyncSession) -> None:
        value = await get_quota_setting_int(
            test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD, default=3000
        )
        assert value == 3000

    async def test_raises_without_default_when_row_missing(
        self, test_session: AsyncSession
    ) -> None:
        with pytest.raises(NotFoundError):
            await get_quota_setting_int(test_session, QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD)


class TestGetRoleDefaultLimitUsd:
    async def test_falls_back_to_constant_when_row_missing(
        self, test_session: AsyncSession
    ) -> None:
        result = await get_role_default_limit_usd(test_session, "worker")
        assert result == Decimal("200")

    async def test_uses_db_row_when_present(self, test_session: AsyncSession) -> None:
        await _seed_setting(test_session, "DEFAULT_LIMIT_WORKER_USD", "777")
        result = await get_role_default_limit_usd(test_session, "worker")
        assert result == Decimal("777")

    async def test_unknown_role_returns_zero_without_db_query(
        self, test_session: AsyncSession
    ) -> None:
        result = await get_role_default_limit_usd(test_session, "nonexistent")
        assert result == Decimal("0")
