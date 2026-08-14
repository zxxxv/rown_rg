from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.core.clock import now
from src.core.exceptions import AuthenticationError
from src.db.models.user import User
from src.infrastructure.auth.jwt_handler import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# '현재 접속중' 판정용 하트비트 갱신 주기 — 요청마다 쓰지 않고 이 간격으로 스로틀한다.
LAST_SEEN_UPDATE_INTERVAL = timedelta(seconds=60)


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    access_token = token or request.cookies.get("access_token")
    if not access_token:
        raise AuthenticationError(message="missing access token", code="MISSING_TOKEN")

    token_data = decode_token(access_token)
    if token_data.token_type != "access":
        raise AuthenticationError(message="access token required", code="WRONG_TOKEN_TYPE")

    user = await session.get(User, token_data.user_id)
    if user is None:
        raise AuthenticationError(message="user not found", code="USER_NOT_FOUND")

    # 하트비트: 커밋은 opener(get_async_session)가 요청 성공 시 수행한다.
    current = now()
    if user.last_seen_at is None or current - user.last_seen_at >= LAST_SEEN_UPDATE_INTERVAL:
        user.last_seen_at = current
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_active:
        raise AuthenticationError(message="inactive user", code="INACTIVE_USER")
    return current_user
