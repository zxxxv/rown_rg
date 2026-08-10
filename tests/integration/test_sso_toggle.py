"""SSO 사용 토글 — 끄면 버튼도 엔드포인트도 사라진다.

IdP 값을 넣기 전이나 점검 중에 버튼만 살아 있으면 사용자는 오류만 본다.
로그인 화면은 /auth/sso/status(인증 불필요)를 보고 버튼을 띄운다.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.core import app_settings

pytestmark = pytest.mark.integration


class TestSsoStatus:
    async def test_status_is_public(self, test_client: AsyncClient) -> None:
        # 로그인 전 화면이 부르는 엔드포인트다 - 인증을 요구하면 안 된다.
        resp = await test_client.get("/api/v1/auth/sso/status")
        assert resp.status_code == 200
        assert isinstance(resp.json()["enabled"], bool)

    async def test_disabled_when_toggle_off(
        self, test_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_settings, "get_bool", lambda key: False)
        resp = await test_client.get("/api/v1/auth/sso/status")
        assert resp.json()["enabled"] is False

    async def test_disabled_when_idp_not_configured(
        self, test_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 켜져 있어도 IdP 값이 비면 눌러봐야 실패한다 - 그것도 꺼진 것으로 본다.
        monkeypatch.setattr(app_settings, "get_bool", lambda key: True)
        monkeypatch.setattr(app_settings, "get_str", lambda key, default="": "")
        resp = await test_client.get("/api/v1/auth/sso/status")
        assert resp.json()["enabled"] is False

    async def test_login_endpoint_blocked_when_off(
        self, test_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_settings, "get_bool", lambda key: False)
        resp = await test_client.get("/api/v1/auth/saml/login", follow_redirects=False)
        assert resp.status_code == 404
