from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends

from src.api.dependencies.auth import get_current_active_user
from src.core.exceptions import AuthorizationError
from src.db.models.user import User


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _checker(
        current_user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if current_user.role not in roles:
            raise AuthorizationError(
                message=f"requires one of roles: {list(roles)}",
                code="FORBIDDEN",
            )
        return current_user

    return _checker


ROLE_LEVEL: dict[str, int] = {
    "viewer": 0,
    "worker": 1,
    "admin": 2,
    "super_admin": 3,
}


def assert_can_assign_role(actor: User, target_role: str) -> None:
    """
    호출자(actor)는 자기 이하(같은 레벨 포함) 역할만 부여할 수 있음 - 권한 상승 차단
    """
    if ROLE_LEVEL.get(actor.role, -1) < ROLE_LEVEL.get(target_role, len(ROLE_LEVEL)):
        raise AuthorizationError(
            message=f"'{actor.role}'은(는) '{target_role}' 역할을 부여할 수 없습니다",
            code="FORBIDDEN",
        )


def assert_can_manage_user(actor: User, target: User) -> None:
    """
    호출자(actor)는 자기보다 높은 역할의 유저를 수정/관리할 수 없음
    """
    if ROLE_LEVEL.get(actor.role, -1) < ROLE_LEVEL.get(target.role, len(ROLE_LEVEL)):
        raise AuthorizationError(
            message=f"'{actor.role}'은(는) '{target.role}' 사용자를 수정할 수 없습니다",
            code="FORBIDDEN",
        )
