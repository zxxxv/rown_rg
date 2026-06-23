import base64
import re
import traceback
import zlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import assert_can_assign_role, require_role
from src.api.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutResponse,
    RefreshRequest,
    TokenPair,
)
from src.api.schemas.user import UserCreate, UserRead
from src.core.clock import now
from src.core.config import settings
from src.core.exceptions import AuthenticationError, ValidationError
from src.db.models.user import User
from src.infrastructure.auth import (
    jwt_handler,
    lockout_handler,
    password_handler,
    refresh_token_handler,
    totp_handler,
)
from src.infrastructure.auth.saml import init_saml_auth

router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = settings.refresh_token_expire_days


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """access/refresh를 HttpOnly 쿠키로 설정한다(일반 로그인·SSO·refresh 공통)."""
    is_secure = not settings.is_local
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


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> User:
    # 계층 기반: 호출자는 자기보다 낮은 역할만 부여할 수 있다(권한 상승 차단).
    assert_can_assign_role(current_user, data.role)
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
    response: Response,
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

    if user.password_hash is None:
        raise AuthenticationError(
            message="비밀번호 로그인이 설정되지 않은 계정입니다. 네이버웍스로 로그인하세요",
            code="SSO_ONLY_ACCOUNT",
        )

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

    refresh_token = await refresh_token_handler.issue(session, user.id)
    access_token = jwt_handler.create_access_token(user.id)
    _set_auth_cookies(response, access_token, refresh_token)
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh_access_token(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    data: RefreshRequest | None = None,
) -> TokenPair:
    # 쿠키(브라우저) 또는 body(API 클라이언트) 어느 쪽이든 refresh 토큰을 받는다.
    refresh_token = (data.refresh_token if data else None) or request.cookies.get("refresh_token")
    if not refresh_token:
        raise AuthenticationError(message="refresh token이 없습니다", code="MISSING_TOKEN")

    # RTR: 검증·회전 + 재사용 감지. 옛 refresh는 소비되고 새 refresh가 발급된다.
    user_id, new_refresh_token = await refresh_token_handler.rotate(session, refresh_token)

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")

    access_token = jwt_handler.create_access_token(user.id)
    _set_auth_cookies(response, access_token, new_refresh_token)
    return TokenPair(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=UserRead.model_validate(user),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    response: Response,
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> LogoutResponse:
    # RTR: 유저의 모든 refresh 토큰 폐기(전체 세션 종료) 후 SSO 쿠키 삭제
    await refresh_token_handler.revoke_all_for_user(session, current_user.id)
    is_secure = not settings.is_local
    response.delete_cookie("access_token", httponly=True, secure=is_secure, samesite="lax")
    response.delete_cookie("refresh_token", httponly=True, secure=is_secure, samesite="lax")
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
    prod_base_url = settings.saml_base_url
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
        react_frontend_url = settings.react_frontend_url
        return RedirectResponse(url=auth.login(return_to=react_frontend_url))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SAML 로그인 URL 생성 실패: {str(e)}",
        )


@router.post("/saml/acs")
async def saml_acs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
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
        is_local = settings.is_local
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
            stmt = select(User).where(User.email == user_email)
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                user = User(
                    email=user_email,
                    name=user_email.split("@")[0],
                    role="viewer",
                    password_hash=None,
                    is_active=True,
                )
                session.add(user)
                await session.flush()  # user.id(UUID) 채우기
            elif not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="비활성화된 계정입니다.",
                )

            user.last_login_at = now()

            # 일반 로그인과 동일하게 토큰 발급 (sub=user.id(UUID), role 포함)
            access_token = jwt_handler.create_access_token(user.id)
            refresh_token = await refresh_token_handler.issue(session, user.id)

            # 리다이렉트 설정
            react_frontend_url = settings.react_frontend_url
            response = RedirectResponse(
                url=f"{react_frontend_url}/callback",
                status_code=status.HTTP_303_SEE_OTHER,
            )

            # HttpOnly 쿠키 설정
            _set_auth_cookies(response, access_token, refresh_token)

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
