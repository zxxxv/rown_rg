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
    token_type: TokenType
    # refresh 토큰 회전(RTR)용 - access 토큰에는 없음
    jti: UUID | None = None
    family_id: UUID | None = None


def _encode(payload: dict[str, Any], expires_delta: timedelta) -> str:
    now = clock_now()
    to_encode = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: UUID) -> str:
    return _encode(
        {"sub": str(user_id), "type": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: UUID, jti: UUID, family_id: UUID) -> str:
    return _encode(
        {
            "sub": str(user_id),
            "type": "refresh",
            "jti": str(jti),
            "fam": str(family_id),
        },
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

    return TokenData(
        user_id=user_id,
        token_type=token_type,
        jti=_maybe_uuid(payload.get("jti")),
        family_id=_maybe_uuid(payload.get("fam")),
    )


def _maybe_uuid(value: Any) -> UUID | None:
    try:
        return UUID(value) if value else None
    except (TypeError, ValueError):
        return None
