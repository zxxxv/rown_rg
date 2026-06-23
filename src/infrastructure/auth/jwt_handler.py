from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from jose import JWTError, jwt
from pydantic import BaseModel

from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.exceptions import AuthenticationError

TokenType = Literal["access", "refresh"]


class TokenData(BaseModel):
    user_id: UUID
    role: str | None = None
    token_type: TokenType


def _encode(payload: dict[str, Any], expires_delta: timedelta) -> str:
    now = clock_now()
    to_encode = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: UUID, role: str) -> str:
    return _encode(
        {"sub": str(user_id), "role": role, "type": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: UUID) -> str:
    return _encode(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as e:
        raise AuthenticationError(message="invalid or expired token", code="INVALID_TOKEN") from e

    sub = payload.get("sub")
    token_type = payload.get("type")
    if not sub or token_type not in ("access", "refresh"):
        raise AuthenticationError(message="invalid token payload", code="INVALID_TOKEN")

    try:
        user_id = UUID(sub)
    except (TypeError, ValueError) as e:
        raise AuthenticationError(message="invalid token subject", code="INVALID_TOKEN") from e

    return TokenData(user_id=user_id, role=payload.get("role"), token_type=token_type)
