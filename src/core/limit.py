from decimal import Decimal

# 역할별 기본 월 한도(USD). user_quotas에 행이 없을 때의 폴백값.
# 관리자가 개별 한도를 지정하면(user_quotas 행) 그 값이 우선한다.
#
# 이 값들은 또한 quota_settings(DEFAULT_LIMIT_*_USD) 행이 아직 없을 때
# (마이그레이션 미실행 등)의 폴백 하한값이기도 하다 — 정상 운영 중에는 DB 값을
# 우선한다(src.services.quota_settings.get_role_default_limit_usd).
# 이 모듈 자체는 세션이 필요 없는 순수 상수 모듈로 유지한다 — DB 인지 로직은 서비스
# 레이어에만 둔다.
DEFAULT_ROLE_LIMIT_USD: dict[str, Decimal] = {
    "super_admin": Decimal("500"),
    "admin": Decimal("300"),
    "worker": Decimal("200"),
    "viewer": Decimal("50"),
}


def default_limit_for(role: str) -> Decimal:
    """역할별 기본 월 한도(USD). 알 수 없는 역할은 0."""
    return DEFAULT_ROLE_LIMIT_USD.get(role, Decimal("0"))
