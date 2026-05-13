from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import require_role
from src.api.schemas.auth import (
    AccessToken,
    ChangePasswordRequest,
    LoginRequest,
    LogoutResponse,
    RefreshRequest,
    TokenPair,
)
from src.api.schemas.user import UserCreate, UserRead
from src.core.exceptions import AuthenticationError, ValidationError
from src.db.models.user import User
from src.infrastructure.auth import (
    jwt_handler,
    lockout_handler,
    password_handler,
    totp_handler,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> User:
    password_handler.validate_password_policy(data.password)
    user = User(
        email=data.email,
        name=data.name,
        role=data.role,
        password_hash=password_handler.hash_password(data.password),
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise ValidationError(message="이미 사용 중인 이메일입니다", code="EMAIL_DUPLICATE") from e
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenPair)
async def login(
    data: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> TokenPair:
    stmt = select(User).where(User.email == data.email)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise AuthenticationError(
            message="이메일 또는 비밀번호가 올바르지 않습니다",
            code="INVALID_CREDENTIALS",
        )

    if lockout_handler.check_locked(user):
        remaining = lockout_handler.remaining_seconds(user)
        raise AuthenticationError(
            message=f"계정이 잠겨 있습니다. {remaining}초 후 다시 시도하세요",
            code="ACCOUNT_LOCKED",
        )

    if not user.is_active:
        raise AuthenticationError(message="비활성화된 계정입니다", code="INACTIVE_USER")

    if not password_handler.verify_password(data.password, user.password_hash):
        lockout_handler.record_failed_attempt(user)
        await session.commit()
        raise AuthenticationError(
            message="이메일 또는 비밀번호가 올바르지 않습니다",
            code="INVALID_CREDENTIALS",
        )

    if user.totp_secret:
        if not data.totp_code or not totp_handler.verify_totp(user.totp_secret, data.totp_code):
            lockout_handler.record_failed_attempt(user)
            await session.commit()
            raise AuthenticationError(message="TOTP 코드가 올바르지 않습니다", code="INVALID_TOTP")

    lockout_handler.reset_attempts(user)
    user.last_login_at = _now_naive()

    return TokenPair(
        access_token=jwt_handler.create_access_token(user.id, user.role),
        refresh_token=jwt_handler.create_refresh_token(user.id),
        user=UserRead.model_validate(user),
    )


@router.post("/refresh", response_model=AccessToken)
async def refresh_access_token(
    data: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AccessToken:
    token_data = jwt_handler.decode_token(data.refresh_token)
    if token_data.token_type != "refresh":
        raise AuthenticationError(message="refresh token이 아닙니다", code="WRONG_TOKEN_TYPE")

    user = await session.get(User, token_data.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")

    return AccessToken(access_token=jwt_handler.create_access_token(user.id, user.role))


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    _: Annotated[User, Depends(get_current_active_user)],
) -> LogoutResponse:
    return LogoutResponse(success=True)


@router.get("/me", response_model=UserRead)
async def me(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    return current_user


@router.post("/change-password", response_model=LogoutResponse)
async def change_password(
    data: ChangePasswordRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    _session: Annotated[AsyncSession, Depends(get_async_session)],
) -> LogoutResponse:
    if not password_handler.verify_password(data.current_password, current_user.password_hash):
        raise AuthenticationError(
            message="현재 비밀번호가 올바르지 않습니다", code="INVALID_CREDENTIALS"
        )
    password_handler.validate_password_policy(data.new_password)
    current_user.password_hash = password_handler.hash_password(data.new_password)
    current_user.password_changed_at = _now_naive()
    return LogoutResponse(success=True)
