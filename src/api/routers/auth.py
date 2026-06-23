import base64
import os
import re
import traceback
import zlib
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
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
from src.core.clock import now
from src.core.exceptions import AuthenticationError, ValidationError
from src.db.models.user import User
from src.infrastructure.auth import (
    jwt_handler,
    lockout_handler,
    password_handler,
    totp_handler,
)
from src.infrastructure.auth.saml import init_saml_auth

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-32-chars-or-more")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))


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
    user.last_login_at = now()

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
    current_user.password_changed_at = now()
    return LogoutResponse(success=True)


def get_base_url(request: Request) -> str:
    prod_base_url = os.getenv("SAML_BASE_URL")
    if prod_base_url:
        return prod_base_url

    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:8000"))
    return f"{proto}://{host}"


@router.get("/saml/login")
async def saml_login(request: Request):
    try:
        base_url = get_base_url(request)
        auth = await init_saml_auth(request, base_url)
        return RedirectResponse(url=auth.login())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SAML 로그인 URL 생성 실패: {str(e)}",
        )


@router.post("/saml/acs")
async def saml_acs(request: Request):
    try:
        form_data = await request.form()
        saml_response_b64 = form_data.get("SAMLResponse")
        raw_xml = ""

        if saml_response_b64:
            if not isinstance(saml_response_b64, str):
                file_content = await saml_response_b64.read()
                saml_response_str = file_content.decode("utf-8")
            else:
                saml_response_str = saml_response_b64

            try:
                decoded_bytes = base64.b64decode(saml_response_str)
                try:
                    raw_xml = zlib.decompress(decoded_bytes, -15).decode("utf-8")
                except Exception:
                    raw_xml = decoded_bytes.decode("utf-8")
            except Exception:
                pass

        user_email = None
        is_local = os.getenv("ENVIRONMENT", "production") == "local"
        base_url = get_base_url(request)

        try:
            auth = await init_saml_auth(request, base_url)
            auth.process_response()
            errors = auth.get_errors()

            if not errors and auth.is_authenticated():
                user_email = auth.get_nameid()
            elif not is_local:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"SAML 검증 실패: {auth.get_last_error_reason()}",
                )
        except HTTPException:
            raise
        except Exception as library_err:
            if not is_local:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"SAML 인증 처리 중 예외 발생: {str(library_err)}",
                )

        if not user_email and raw_xml:
            if is_local:
                nameid_match = re.search(r"<[^>]*NameID[^>]*>([^<]+)</[^>]*NameID>", raw_xml)
                if nameid_match:
                    user_email = nameid_match.group(1).strip()
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="유효하지 않은 인증 정보입니다.",
                )

        if user_email:
            # 토큰 생성
            access_token_expires = datetime.now(UTC) + timedelta(
                minutes=ACCESS_TOKEN_EXPIRE_MINUTES
            )
            access_payload = {
                "sub": user_email,
                "exp": access_token_expires,
                "type": "access",
            }
            access_token = jwt.encode(access_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

            refresh_token_expires = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
            refresh_payload = {
                "sub": user_email,
                "exp": refresh_token_expires,
                "type": "refresh",
            }
            refresh_token = jwt.encode(refresh_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

            # 리다이렉트 설정
            react_frontend_url = os.getenv("REACT_FRONTEND_URL", "http://localhost:5173")
            response = RedirectResponse(
                url=f"{react_frontend_url}/callback",
                status_code=status.HTTP_303_SEE_OTHER,
            )

            # HttpOnly 쿠키 설정
            is_secure = not is_local

            response.set_cookie(
                key="access_token",
                value=access_token,
                httponly=True,
                secure=is_secure,
                samesite="lax",
                max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                httponly=True,
                secure=is_secure,
                samesite="lax",
                max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            )

            return response

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증에 실패했습니다.",
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"서버 내부 오류 발생: {str(e)}",
        )


@router.post("/saml/refresh")
async def saml_refresh(request: Request):
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token이 없습니다."
        )

    try:
        payload = jwt.decode(refresh_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효하지 않은 토큰입니다.",
            )

        user_email = payload.get("sub")
        is_local = os.getenv("ENVIRONMENT", "production") == "local"
        is_secure = not is_local

        current_time = datetime.now(UTC)

        access_token_expires = current_time + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_payload = {
            "sub": user_email,
            "exp": access_token_expires,
            "type": "access",
        }
        new_access_token = jwt.encode(access_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        refresh_token_expires = current_time + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        refresh_payload = {
            "sub": user_email,
            "exp": refresh_token_expires,
            "type": "refresh",
        }
        new_refresh_token = jwt.encode(refresh_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        response = JSONResponse(content={"status": "ok"})

        response.set_cookie(
            key="access_token",
            value=new_access_token,
            httponly=True,
            secure=is_secure,
            samesite="lax",
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=is_secure,
            samesite="lax",
            max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        )

        return response

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token이 만료됐습니다. 다시 로그인해주세요.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰입니다."
        )
