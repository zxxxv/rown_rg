"""IP 화이트리스트 입력 검증 — CIDR 정규화·스키마 validator.

라우터 통합 테스트(권한·DB)는 테스트 DB 인프라가 생기면 별도로 추가한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.api.routers.admin import _normalize_cidr
from src.api.schemas.admin import IpWhitelistCreateInput
from src.core.exceptions import ValidationError


class TestNormalizeCidr:
    def test_single_ip_becomes_host_network(self):
        assert _normalize_cidr("192.168.0.10") == "192.168.0.10/32"

    def test_cidr_passes_through(self):
        assert _normalize_cidr("10.0.0.0/24") == "10.0.0.0/24"

    def test_host_bits_normalized(self):
        # strict=False — 호스트 비트가 켜진 입력도 네트워크 주소로 정규화.
        assert _normalize_cidr("10.0.0.5/24") == "10.0.0.0/24"

    def test_ipv6(self):
        assert _normalize_cidr("::1") == "::1/128"

    def test_whitespace_stripped(self):
        assert _normalize_cidr("  1.2.3.4  ") == "1.2.3.4/32"

    @pytest.mark.parametrize("bad", ["", "abc", "999.1.1.1", "10.0.0.0/40"])
    def test_invalid_raises_422(self, bad: str):
        with pytest.raises(ValidationError) as exc:
            _normalize_cidr(bad)
        assert exc.value.code == "INVALID_CIDR"


class TestIpWhitelistCreateInput:
    def test_naive_expires_rejected(self):
        with pytest.raises(PydanticValidationError):
            IpWhitelistCreateInput(ip_cidr="1.2.3.4", expires_at=datetime(2026, 12, 31))

    def test_aware_expires_accepted(self):
        data = IpWhitelistCreateInput(
            ip_cidr="1.2.3.4", expires_at=datetime(2026, 12, 31, tzinfo=UTC)
        )
        assert data.expires_at is not None and data.expires_at.tzinfo is not None
