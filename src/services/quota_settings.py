"""
quota_settings 테이블 조회 헬퍼 — 기존 config 상수 참조를 대체.

`config.MAX_ORG_QUOTA` 식의 직접 참조 대신 이 모듈의 `get_quota_setting*`을 거치도록
전환한다. 매 조회마다 DB 왕복하지 않도록 프로세스 내 인메모리 캐시를 두고,
admin PATCH가 성공하면 `invalidate_quota_setting_cache`로 해당 key의 캐시를
즉시 비워 다음 조회에서 새 값이 반영되게 한다.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.core.limit import default_limit_for
from src.core.quota_settings import QuotaSettingKey, quota_key_for_role
from src.db.models.quota_setting import QuotaSettings

# 이 캐시는 프로세스 로컬 메모리(파이썬 dict)이며 TTL이 없다 — 무효화되거나
# 프로세스가 재시작되기 전까지 값을 무기한 들고 있는다. 멀티 워커/멀티 인스턴스로
# 배포될 경우 PATCH로 인한 invalidate_quota_setting_cache 호출은 그 요청을 처리한
# 프로세스의 캐시만 비우고 다른 프로세스에는 전파되지 않으므로, 인스턴스 간 값이
# 갈릴 수 있다. 스케일아웃 전에는 Redis 등 공유 스토어 기반 캐시 또는 pub/sub
# 무효화 방식으로 재검토가 필요하다.
# TODO(scale-out): replace in-process cache with shared cache before
# multi-instance deployment. See #14.
_cache: dict[str, str] = {}


def invalidate_quota_setting_cache(key: str | None = None) -> None:
    """캐시를 무효화한다. key를 생략하면 전체를 비운다."""
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


async def get_quota_setting(
    session: AsyncSession, key: QuotaSettingKey, *, default: str | None = None
) -> str:
    """
    quota_settings에서 key의 현재 값을 조회한다(캐시 우선, 없으면 DB 조회 후 채움).

    row가 없을 때 `default`가 주어지면 그 값을 반환한다 — 단, 캐시에는 넣지 않는다.
    이는 마이그레이션 미실행/테스트 DB 등 row가 "아직" 없는 일시적 상태를 위한 것이므로,
    row가 나중에 생기면 다음 호출에서 즉시 실제 DB 값으로 자연 전환되어야 한다
    (self-healing). default가 없으면 기존과 동일하게 NotFoundError.

    quota_settings 테이블 자체가 아직 없는 경우(마이그레이션 0019 미실행)도 row가 없는
    것과 동일하게 취급한다 — SAVEPOINT(begin_nested)로 감싸 조회하므로, 테이블이 없어
    쿼리가 실패해도 그 실패는 이 SAVEPOINT로만 롤백되고 호출자의 바깥 트랜잭션(다른
    커밋 대기 중인 변경사항 포함)은 영향받지 않는다.
    """
    cached = _cache.get(key.value)
    if cached is not None:
        return cached

    try:
        async with session.begin_nested():
            row = (
                await session.execute(select(QuotaSettings).where(QuotaSettings.key == key.value))
            ).scalar_one_or_none()
    except ProgrammingError:
        row = None

    if row is None:
        if default is not None:
            return default
        raise NotFoundError(
            message=f"quota_settings에 '{key.value}' 값이 없습니다",
            code="QUOTA_SETTING_NOT_FOUND",
        )

    _cache[key.value] = row.value
    return row.value


async def get_quota_setting_int(
    session: AsyncSession, key: QuotaSettingKey, *, default: int | None = None
) -> int:
    """get_quota_setting의 정수 변환 버전 — 모든 quota_settings 값은 정수 도메인이다."""
    value = await get_quota_setting(
        session, key, default=str(default) if default is not None else None
    )
    return int(value)


async def get_role_default_limit_usd(session: AsyncSession, role: str) -> Decimal:
    """
    역할별 기본 월 한도(USD) — quota_settings DB 값 우선, row가 없으면
    core.limit.default_limit_for의 상수값으로 폴백한다(알 수 없는 역할도 동일하게 폴백).
    """
    fallback = default_limit_for(role)
    key = quota_key_for_role(role)
    if key is None:
        return fallback
    value = await get_quota_setting_int(session, key, default=int(fallback))
    return Decimal(value)
