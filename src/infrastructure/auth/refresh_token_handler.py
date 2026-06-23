"""Refresh Token Rotation(RTR) — 서버 측 상태 기반 회전·재사용 탐지.

- issue: 로그인 시 새 토큰 패밀리를 시작하고 refresh JWT 발급
- rotate: refresh JWT 검증 후 회전(옛 토큰 소비, 새 토큰 발급). 이미 소비된 토큰의
  재제출(재사용)이 감지되면 해당 패밀리 전체를 폐기하고 거부
- revoke_all_for_user: 로그아웃 시 유저의 모든 활성 refresh 토큰 폐기(전체 세션 종료)

재사용 감지·방어적 폐기는 요청을 거부(raise)하기 전에 commit 해야 한다. 그렇지 않으면
get_async_session의 예외 롤백에 폐기 UPDATE가 함께 사라진다.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.exceptions import AuthenticationError
from src.db.models.refresh_token import RefreshToken
from src.infrastructure.auth import jwt_handler


def _expires_at():
    return clock_now() + timedelta(days=settings.refresh_token_expire_days)


async def issue(session: AsyncSession, user_id: uuid.UUID) -> str:
    """새 토큰 패밀리를 시작하고 refresh JWT를 발급한다(로그인/SSO 콜백)."""
    jti = uuid.uuid4()
    family_id = uuid.uuid4()
    session.add(
        RefreshToken(id=jti, user_id=user_id, family_id=family_id, expires_at=_expires_at())
    )
    return jwt_handler.create_refresh_token(user_id, jti, family_id)


async def rotate(session: AsyncSession, refresh_jwt: str) -> tuple[uuid.UUID, str]:
    """refresh JWT를 검증·회전하고 (user_id, 새 refresh JWT)를 반환한다.

    재사용(이미 회전된 토큰의 재제출)이 감지되면 패밀리 전체를 폐기하고 거부한다.
    JWT 만료/위조는 decode_token이 AuthenticationError로 처리한다.
    """
    token_data = jwt_handler.decode_token(refresh_jwt)
    if token_data.token_type != "refresh" or token_data.jti is None:
        raise AuthenticationError(message="refresh token이 아닙니다", code="WRONG_TOKEN_TYPE")

    row = await session.get(RefreshToken, token_data.jti)

    if row is None or row.revoked:
        # 알 수 없거나 이미 폐기된 토큰 → 패밀리를 알면 방어적으로 폐기
        if token_data.family_id is not None:
            await _revoke_family(session, token_data.family_id)
            await session.commit()
        raise AuthenticationError(message="유효하지 않은 refresh token입니다", code="INVALID_TOKEN")

    if row.used:
        # 재사용 감지: 도난 가능성 → 패밀리 전체 폐기 후 거부
        await _revoke_family(session, row.family_id)
        await session.commit()
        raise AuthenticationError(
            message="refresh token 재사용이 감지되어 세션을 종료합니다",
            code="TOKEN_REUSE_DETECTED",
        )

    # 정상 회전: 옛 토큰 소비 처리, 같은 패밀리로 새 토큰 발급(commit은 호출부 의존성이 수행)
    row.used = True
    new_jti = uuid.uuid4()
    session.add(
        RefreshToken(
            id=new_jti,
            user_id=row.user_id,
            family_id=row.family_id,
            expires_at=_expires_at(),
        )
    )
    new_refresh = jwt_handler.create_refresh_token(row.user_id, new_jti, row.family_id)
    return row.user_id, new_refresh


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """유저의 모든 활성 refresh 토큰을 폐기한다(로그아웃 = 전체 세션 종료)."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
