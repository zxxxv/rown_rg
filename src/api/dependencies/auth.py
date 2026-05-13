from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.core.exceptions import AuthenticationError
from src.db.models.user import User
from src.infrastructure.auth.jwt_handler import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    if not token:
        raise AuthenticationError(message="missing access token", code="MISSING_TOKEN")

    token_data = decode_token(token)
    if token_data.token_type != "access":
        raise AuthenticationError(message="access token required", code="WRONG_TOKEN_TYPE")

    user = await session.get(User, token_data.user_id)
    if user is None:
        raise AuthenticationError(message="user not found", code="USER_NOT_FOUND")
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_active:
        raise AuthenticationError(message="inactive user", code="INACTIVE_USER")
    return current_user
