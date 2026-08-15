from enum import StrEnum
from typing import NamedTuple


class QuotaSettingKey(StrEnum):
    """
    quota_settings.key 화이트리스트

    기존 config 상수(Settings.org_monthly_cost_limit_usd,
    core.limit.DEFAULT_ROLE_LIMIT_USD)를 대체한다. 여기 없는 key는
    조회/수정 모두 거부된다.
    """

    ORG_MONTHLY_COST_LIMIT_USD = "ORG_MONTHLY_COST_LIMIT_USD"
    DEFAULT_LIMIT_SUPER_ADMIN_USD = "DEFAULT_LIMIT_SUPER_ADMIN_USD"
    DEFAULT_LIMIT_ADMIN_USD = "DEFAULT_LIMIT_ADMIN_USD"
    DEFAULT_LIMIT_WORKER_USD = "DEFAULT_LIMIT_WORKER_USD"
    # viewer 한도는 설정 대상이 아니다 — 읽기 전용 역할이라 LLM 비용 경로가 없어
    # $0 고정(core.limit). 여기서 빠졌으므로 조회 캐시·수정 모두 거부된다(0040에서
    # 기존 시딩 행 삭제). 조정이 필요해지면 역할 설계부터 다시 본다(2026-08-15 결정).


class QuotaSettingRule(NamedTuple):
    min_value: int
    max_value: int
    description: str


# key별 값 검증 규칙 — 전부 "양의 정수(USD)" 도메인이므로 정수 파싱 + 범위 검증만 수행한다.
QUOTA_SETTING_RULES: dict[QuotaSettingKey, QuotaSettingRule] = {
    QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD: QuotaSettingRule(
        1, 1_000_000, "조직 전체 월 비용 한도(USD)"
    ),
    QuotaSettingKey.DEFAULT_LIMIT_SUPER_ADMIN_USD: QuotaSettingRule(
        1, 100_000, "super_admin 역할 기본 월 한도(USD)"
    ),
    QuotaSettingKey.DEFAULT_LIMIT_ADMIN_USD: QuotaSettingRule(
        1, 100_000, "admin 역할 기본 월 한도(USD)"
    ),
    QuotaSettingKey.DEFAULT_LIMIT_WORKER_USD: QuotaSettingRule(
        1, 100_000, "worker 역할 기본 월 한도(USD)"
    ),
}

# 마이그레이션 초기 시딩값 — 이관 전 config 상수의 실제 기본값을 그대로 옮긴다.
# (Settings.org_monthly_cost_limit_usd=3000, core.limit.DEFAULT_ROLE_LIMIT_USD)
QUOTA_SETTING_SEED_VALUES: dict[str, str] = {
    QuotaSettingKey.ORG_MONTHLY_COST_LIMIT_USD.value: "3000",
    QuotaSettingKey.DEFAULT_LIMIT_SUPER_ADMIN_USD.value: "500",
    QuotaSettingKey.DEFAULT_LIMIT_ADMIN_USD.value: "300",
    QuotaSettingKey.DEFAULT_LIMIT_WORKER_USD.value: "200",
}


# 역할 → 기본 한도 quota_settings key 매핑. core.limit.DEFAULT_ROLE_LIMIT_USD와 동일한
# 역할 집합을 다룬다. 알 수 없는 역할은 매핑에 없음(quota_key_for_role은 None 반환).
QUOTA_KEY_FOR_ROLE: dict[str, QuotaSettingKey] = {
    "super_admin": QuotaSettingKey.DEFAULT_LIMIT_SUPER_ADMIN_USD,
    "admin": QuotaSettingKey.DEFAULT_LIMIT_ADMIN_USD,
    "worker": QuotaSettingKey.DEFAULT_LIMIT_WORKER_USD,
    # viewer는 매핑 없음 → quota_key_for_role이 None → core.limit 상수($0) 폴백.
}


def quota_key_for_role(role: str) -> QuotaSettingKey | None:
    """역할에 대응하는 quota_settings key. 알 수 없는 역할이면 None."""
    return QUOTA_KEY_FOR_ROLE.get(role)


def parse_quota_setting_key(key: str) -> QuotaSettingKey:
    """key가 화이트리스트에 있는지 확인한다. 없으면 ValueError."""
    try:
        return QuotaSettingKey(key)
    except ValueError:
        raise ValueError(f"화이트리스트에 없는 key입니다: {key!r}") from None


def validate_quota_setting_value(key: QuotaSettingKey, value: str) -> int:
    """value가 key의 검증 규칙(정수·범위)을 만족하는지 확인하고 파싱된 정수를 반환한다."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{key.value}' 값은 정수여야 합니다: {value!r}") from None

    rule = QUOTA_SETTING_RULES[key]
    if not (rule.min_value <= parsed <= rule.max_value):
        raise ValueError(
            f"'{key.value}' 값은 {rule.min_value}~{rule.max_value} 범위여야 합니다: {parsed}"
        )
    return parsed
