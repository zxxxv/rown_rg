from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import (
    assert_can_assign_role,
    assert_can_manage_user,
    require_role,
)
from src.api.schemas.user import UserRead, UserUpdate
from src.core.exceptions import AuthorizationError, NotFoundError
from src.db.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
async def list_users(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin", "admin"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[User]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    if current_user.id != user_id and current_user.role not in ("super_admin", "admin"):
        raise AuthorizationError(message="권한이 없습니다", code="FORBIDDEN")
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    return user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role("super_admin", "admin"))],
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    # 상위 역할 유저는 건드릴 수 없다(admin이 super_admin 비활성화·수정 차단).
    assert_can_manage_user(current_user, user)
    if data.role is not None:
        assert_can_assign_role(current_user, data.role)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    await session.flush()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_user(
    user_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role("super_admin"))],
) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(message="사용자를 찾을 수 없습니다", code="USER_NOT_FOUND")
    user.is_active = False
