import re

import bcrypt

from src.core.exceptions import ValidationError

MIN_LENGTH = 12
MAX_BYTES = 72  # bcrypt hard limit

_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"[A-Z]"), "PASSWORD_NEEDS_UPPER", "비밀번호에 대문자 포함 필수"),
    (re.compile(r"[a-z]"), "PASSWORD_NEEDS_LOWER", "비밀번호에 소문자 포함 필수"),
    (re.compile(r"[0-9]"), "PASSWORD_NEEDS_DIGIT", "비밀번호에 숫자 포함 필수"),
    (
        re.compile(r"[!@#$%^&*(),.?\":{}|<>]"),
        "PASSWORD_NEEDS_SPECIAL",
        "비밀번호에 특수문자 포함 필수",
    ),
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def validate_password_policy(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise ValidationError(
            message=f"비밀번호는 최소 {MIN_LENGTH}자 이상이어야 합니다",
            code="PASSWORD_TOO_SHORT",
        )
    if len(password.encode("utf-8")) > MAX_BYTES:
        raise ValidationError(
            message=f"비밀번호는 최대 {MAX_BYTES}바이트까지 허용됩니다",
            code="PASSWORD_TOO_LONG",
        )
    for regex, code, msg in _RULES:
        if not regex.search(password):
            raise ValidationError(message=msg, code=code)
