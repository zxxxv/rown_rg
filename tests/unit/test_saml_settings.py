"""SAML IdP 설정 출처 — 코드 하드코딩이 아니라 관리자 설정.

IdP 값(Entity ID·SSO URL·인증서)이 saml.py에 박혀 있어 바꿀 때마다 재배포가
필요했다(2026-08-10 지적). LLM 키와 같은 경로(app_settings → .env → 기본값)로 옮겨
프론트 시스템 설정에서 넣을 수 있게 한다.
"""

from __future__ import annotations

from src.core.app_settings import DEF_BY_KEY
from src.infrastructure.auth.saml import get_saml_settings

IDP_KEYS = ("saml_idp_entity_id", "saml_idp_sso_url", "saml_idp_x509cert")


class TestIdpSettingsAreAdminManaged:
    def test_keys_registered_in_admin_settings(self):
        for key in IDP_KEYS:
            assert key in DEF_BY_KEY, key
            assert DEF_BY_KEY[key].group == "SSO"

    def test_settings_come_from_config_not_literals(self, monkeypatch):
        from src.core import app_settings

        monkeypatch.setattr(
            app_settings,
            "get_str",
            lambda key, default="": {
                "saml_idp_entity_id": "https://idp.example/entity",
                "saml_idp_sso_url": "https://idp.example/sso",
                "saml_idp_x509cert": "CERT",
            }.get(key, default),
        )
        idp = get_saml_settings("https://app.example")["idp"]
        assert idp["entityId"] == "https://idp.example/entity"
        assert idp["singleSignOnService"]["url"] == "https://idp.example/sso"
        assert idp["x509cert"] == "CERT"

    def test_sp_urls_follow_base_url(self):
        sp = get_saml_settings("https://app.example")["sp"]
        assert sp["entityId"] == "https://app.example/api/v1/auth/saml/metadata"
        assert sp["assertionConsumerService"]["url"] == "https://app.example/api/v1/auth/saml/acs"
