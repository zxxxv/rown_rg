from __future__ import annotations

import re

from sqlalchemy import CheckConstraint

from src.api.dependencies.permissions import ADMINS, ROLE_LEVEL
from src.api.schemas.user import UserRole
from src.core.types import Role
from src.db.models.user import User

# 사용자 역할의 정본(canonical) 값 집합.
# 이 집합을 바꾸면 DB CheckConstraint 마이그레이션도 함께 갱신해야 한다 (의도적 변경 강제).
EXPECTED_ROLE_VALUES = {
    "super_admin",
    "admin",
    "worker",
    "viewer",
}


def test_role_is_canonical_set() -> None:
    """Role(단일 진실)이 정본 값 집합과 정확히 일치해야 한다."""
    assert {role.value for role in Role} == EXPECTED_ROLE_VALUES


def test_api_user_role_derives_from_enum() -> None:
    """API UserRole은 별도 정의 없이 Role에서 파생되어야 한다."""
    assert UserRole is Role


def test_db_check_constraint_matches_enum() -> None:
    """users.role CheckConstraint 허용 값이 Role과 어긋나지 않아야 한다."""
    constraint = next(
        c
        for c in User.__table__.constraints
        if isinstance(c, CheckConstraint) and str(c.sqltext).startswith("role IN")
    )
    allowed = set(re.findall(r"'([^']*)'", str(constraint.sqltext)))
    assert allowed == {role.value for role in Role}


def test_role_level_covers_every_role() -> None:
    """계층(ROLE_LEVEL)에 빠진 역할이 없어야 한다(신규 역할 추가 시 누락 방지)."""
    assert set(ROLE_LEVEL) == set(Role)


def test_admins_group_is_admin_or_above() -> None:
    """ADMINS 그룹은 admin 이상 역할만 담아야 한다."""
    assert set(ADMINS) == {Role.SUPER_ADMIN, Role.ADMIN}
    assert all(ROLE_LEVEL[role] >= ROLE_LEVEL[Role.ADMIN] for role in ADMINS)
