from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

UserRole = Literal["super_admin", "admin", "worker", "viewer"]


class UserBase(BaseModel):
    email: EmailStr = Field(..., description="이메일 주소")
    name: str = Field(..., min_length=1, max_length=100, description="이름")
    role: UserRole = Field(..., description="권한 역할")


class UserCreate(UserBase):
    password: str = Field(..., min_length=12, description="비밀번호 (최소 12자)")

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("비밀번호에 대문자 포함 필수")
        if not re.search(r"[a-z]", v):
            raise ValueError("비밀번호에 소문자 포함 필수")
        if not re.search(r"[0-9]", v):
            raise ValueError("비밀번호에 숫자 포함 필수")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("비밀번호에 특수문자 포함 필수")
        return v


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    role: UserRole | None = None
    is_active: bool | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
