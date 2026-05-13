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
