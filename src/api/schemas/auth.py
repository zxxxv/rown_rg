from __future__ import annotations

from pydantic import BaseModel, Field

from src.api.schemas.user import UserRead


class LoginRequest(BaseModel):
    login_id: str = Field(..., min_length=1, description="이메일 또는 아이디")
    password: str
    totp_code: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., description="새 비밀번호")


class LogoutResponse(BaseModel):
    success: bool = True


class SsoStatus(BaseModel):
    """로그인 화면이 SSO 버튼을 띄울지 — 인증 없이 조회한다.

    켜짐이면서 IdP 3값이 다 채워졌을 때만 true. 버튼만 살아 있고 누르면 실패하는
    상태를 없애려는 것이다.
    """

    enabled: bool
