from decimal import Decimal

# 역할별 기본 월 한도(USD). user_quotas에 행이 없을 때의 폴백값.
# 관리자가 개별 한도를 지정하면(user_quotas) 그 값이 우선한다.
DEFAULT_ROLE_QUOTA_USD: dict[str, Decimal] = {
    "super_admin": Decimal("500"),
    "admin": Decimal("300"),
    "worker": Decimal("200"),
    "viewer": Decimal("50"),
}


def default_quota_for(role: str) -> Decimal:
    """역할별 기본 월 한도(USD). 알 수 없는 역할은 0."""
    return DEFAULT_ROLE_QUOTA_USD.get(role, Decimal("0"))
