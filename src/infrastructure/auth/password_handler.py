import re

import bcrypt

from src.core.exceptions import ValidationError

MIN_LENGTH = 12
MAX_BYTES = 72  # bcrypt hard limit

_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"[A-Z]"), "PASSWORD_NEEDS_UPPER", "대문자"),
    (re.compile(r"[a-z]"), "PASSWORD_NEEDS_LOWER", "소문자"),
    (re.compile(r"[0-9]"), "PASSWORD_NEEDS_DIGIT", "숫자"),
    (
        re.compile(r"[!@#$%^&*(),.?\":{}|<>]"),
        "PASSWORD_NEEDS_SPECIAL",
        "특수문자(!@#$%^&* 등)",
    ),
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def password_policy_failures(password: str) -> list[tuple[str, str]]:
    """위반한 규칙 전부 — (코드, 사람이 읽는 사유). 통과면 빈 목록."""
    failures: list[tuple[str, str]] = []
    if len(password) < MIN_LENGTH:
        failures.append(("PASSWORD_TOO_SHORT", f"{MIN_LENGTH}자 이상"))
    if len(password.encode("utf-8")) > MAX_BYTES:
        failures.append(("PASSWORD_TOO_LONG", f"{MAX_BYTES}바이트 이하"))
    failures += [(code, label) for regex, code, label in _RULES if not regex.search(password)]
    return failures


def validate_password_policy(password: str) -> None:
    """정책 위반이면 ValidationError — 무엇이 부족한지 전부 알려준다.

    첫 번째 위반만 알려주면 사용자가 고칠 때마다 다음 규칙이 튀어나온다("대문자
    필요" → 고치면 "특수문자 필요" → …). 한 번에 다 말한다(2026-08-10 지적).
    코드는 첫 위반 것을 유지한다 — 기존 클라이언트·테스트가 그 값을 본다.
    """
    failures = password_policy_failures(password)
    if not failures:
        return
    missing = ", ".join(label for _, label in failures)
    raise ValidationError(
        message=f"비밀번호 조건을 확인하세요 — 부족한 항목: {missing}",
        code=failures[0][0],
    )
