"""잠금 면제 - 관리자는 잠기지 않는다(2026-08-20 사용자 결정).

잠긴 계정을 풀 사람이 관리자인데 관리자가 잠기면 DB를 직접 만져야 한다
(실사고: super_admin 5회 실패 → 30분 잠김). 실패 횟수는 계속 센다.
"""

from __future__ import annotations

from datetime import timedelta

from src.core.clock import now
from src.db.models.user import User
from src.infrastructure.auth.lockout_handler import (
    MAX_ATTEMPTS,
    check_locked,
    record_failed_attempt,
)


def _user(role: str) -> User:
    return User(email=f"{role}@test.local", name=role, role=role, password_hash="x")


class TestAdminLockExemption:
    def test_admin_never_locks(self) -> None:
        for role in ("admin", "super_admin"):
            u = _user(role)
            for _ in range(MAX_ATTEMPTS + 3):
                record_failed_attempt(u)
            assert u.locked_until is None
            assert u.failed_login_count == MAX_ATTEMPTS + 3  # 횟수는 계속 센다(감사 흔적)
            assert check_locked(u) is False

    def test_worker_still_locks(self) -> None:
        u = _user("worker")
        for _ in range(MAX_ATTEMPTS):
            record_failed_attempt(u)
        assert u.locked_until is not None
        assert check_locked(u) is True

    def test_stale_lock_on_admin_is_ignored(self) -> None:
        """규칙 변경 전·승격 전에 걸린 잠금도 무시 - 자가 치유."""
        u = _user("admin")
        u.locked_until = now() + timedelta(minutes=30)
        assert check_locked(u) is False
