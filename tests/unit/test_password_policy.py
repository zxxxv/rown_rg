"""비밀번호 정책 — 무엇이 부족한지 한 번에 알려준다.

첫 위반만 알리면 사용자가 고칠 때마다 다음 규칙이 튀어나온다("대문자 필요" →
고치면 "특수문자 필요" → …). 2026-08-10 사용자 지적으로 전체 보고로 바꿨다.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import ValidationError
from src.infrastructure.auth.password_handler import (
    MIN_LENGTH,
    password_policy_failures,
    validate_password_policy,
)


class TestPolicyFailures:
    def test_valid_password_has_no_failures(self):
        assert password_policy_failures("Smoke-2026!!aa") == []

    def test_reports_every_missing_rule_at_once(self):
        labels = [label for _, label in password_policy_failures("short")]
        assert f"{MIN_LENGTH}자 이상" in labels
        assert "대문자" in labels
        assert "숫자" in labels
        assert any("특수문자" in x for x in labels)

    def test_message_lists_all_missing(self):
        with pytest.raises(ValidationError) as exc:
            validate_password_policy("alllowercaseonly")
        message = exc.value.message
        assert "대문자" in message and "숫자" in message and "특수문자" in message

    def test_code_stays_first_violation(self):
        # 기존 클라이언트·테스트가 코드 값을 본다 - 계약을 바꾸지 않는다.
        with pytest.raises(ValidationError) as exc:
            validate_password_policy("short")
        assert exc.value.code == "PASSWORD_TOO_SHORT"

    def test_valid_password_passes(self):
        validate_password_policy("Smoke-2026!!aa")
