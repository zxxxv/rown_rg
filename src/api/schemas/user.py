from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

UserRole = Literal["super_admin", "admin", "worker", "viewer"]


class UserBase(BaseModel):
    email: EmailStr = Field(..., description="이메일 주소")
    name: str = Field(..., min_length=1, max_length=100, description="이름")
    role: UserRole = Field(..., description="권한 역할")


class UserCreate(UserBase):
    password: str = Field(..., description="비밀번호")


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
