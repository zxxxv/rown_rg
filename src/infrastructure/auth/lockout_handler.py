from datetime import timedelta

from src.core.clock import now
from src.db.models.user import User

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 30


def record_failed_attempt(user: User) -> None:
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= MAX_ATTEMPTS:
        user.locked_until = now() + timedelta(minutes=LOCKOUT_MINUTES)


def check_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > now()


def remaining_seconds(user: User) -> int:
    if user.locked_until is None:
        return 0
    delta = user.locked_until - now()
    return max(0, int(delta.total_seconds()))


def reset_attempts(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
