from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends

from src.api.dependencies.auth import get_current_active_user
from src.core.exceptions import AuthorizationError
from src.core.types import Role
from src.db.models.user import User

# "관리자 이상" 역할 그룹 - 유저 목록/조회/수정·계정 생성 권한.
# 엔드포인트 곳곳에 ("super_admin", "admin") 리터럴을 흩뿌리지 않고 이 이름 하나로 참조한다.
ADMINS: tuple[Role, ...] = (Role.SUPER_ADMIN, Role.ADMIN)

# 쓰기 작업(프로젝트 생성·실행·편집·업로드·프롬프트 관리) 허용 역할 - 뷰어는 열람 전용.
# SSO JIT 가입이 viewer로 떨어지므로 뷰어가 쓰기를 시도하는 일은 일상적이다.
WRITERS: tuple[Role, ...] = (Role.SUPER_ADMIN, Role.ADMIN, Role.WORKER)


async def require_writer(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """뷰어(열람 전용) 차단 - 사유를 사람이 읽고 행동할 수 있게 명시한다.

    전에는 쓰기 엔드포인트가 역할을 안 봐서 뷰어도 프로젝트를 만들 수 있었고,
    막더라도 조용히 실패하면 사용자가 버그로 오해한다(2026-08-14 사용자 결정:
    막힌 이유를 확실히 띄운다). 코드가 고유해 프론트가 안내 UI로 구분할 수 있다.
    """
    if current_user.role not in WRITERS:
        raise AuthorizationError(
            message="이 작업에는 작성 권한이 필요합니다 - 관리자에게 권한 상향을 문의하세요",
            code="WRITE_ROLE_REQUIRED",
        )
    return current_user


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
    Role.VIEWER: 0,
    Role.WORKER: 1,
    Role.ADMIN: 2,
    Role.SUPER_ADMIN: 3,
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
