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
