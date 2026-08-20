from datetime import timedelta

from src.core.clock import now
from src.db.models.user import User

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 30

# 관리자 잠금 면제(2026-08-20 사용자 결정) — 잠긴 계정을 풀 사람이 관리자인데 관리자가
# 잠기면 DB를 직접 만져야 한다(실사고: super_admin이 5회 실패로 30분 잠김). 실패
# 횟수는 계속 세되 잠금만 안 건다. 무차별 대입은 여전히 실패 시 지연·로그로 남는다.
_LOCK_EXEMPT_ROLES = ("admin", "super_admin")


def record_failed_attempt(user: User) -> None:
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= MAX_ATTEMPTS and user.role not in _LOCK_EXEMPT_ROLES:
        user.locked_until = now() + timedelta(minutes=LOCKOUT_MINUTES)


def check_locked(user: User) -> bool:
    # 면제 역할은 과거에 걸린 잠금(승격 전·규칙 변경 전)도 무시한다 — 자가 치유.
    if user.role in _LOCK_EXEMPT_ROLES:
        return False
    return user.locked_until is not None and user.locked_until > now()


def remaining_seconds(user: User) -> int:
    if user.locked_until is None:
        return 0
    delta = user.locked_until - now()
    return max(0, int(delta.total_seconds()))


def reset_attempts(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
