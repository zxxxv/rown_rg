from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.core.types import Role

# role은 Role(core)를 단일 진실로 삼아 파생한다. 별도 Literal로 중복 정의하지 않는다.
UserRole = Role


class UserBase(BaseModel):
    email: EmailStr = Field(..., description="이메일 주소")
    name: str = Field(..., min_length=1, max_length=100, description="이름")
    role: UserRole = Field(..., description="권한 역할")


class UserCreate(UserBase):
    password: str = Field(..., description="비밀번호")
    username: str | None = Field(
        None, min_length=3, max_length=50, description="로그인 아이디(선택)"
    )


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    role: UserRole | None = None
    is_active: bool | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    # 출력은 형식 검증을 하지 않는다 — DB에 있는 레거시/테스트 이메일(.local 등) 한 행이
    # EmailStr 검증에 걸리면 목록 전체가 500이 된다. 입력 검증은 UserCreate(EmailStr) 몫.
    email: str
    id: UUID
    # 이메일 대신 로그인에 쓸 수 있는 아이디(없으면 이메일로만 로그인)
    username: str | None
    is_active: bool
    # SSO 전용 계정은 비밀번호가 없다 → 프론트에서 비밀번호 변경 UI 노출 여부 판단에 사용
    has_password: bool
    # 로그인 실패 잠금 만료 시각 — 미래면 잠김. admin 사용자 관리의 잠금 표시·해제 버튼 판단용
    locked_until: datetime | None
    last_login_at: datetime | None
    password_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
