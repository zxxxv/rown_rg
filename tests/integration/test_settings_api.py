"""관리자 설정(app_settings) API — 카탈로그 조회·시크릿 마스킹·저장·유효값 반영·권한."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.core import app_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """인메모리 설정 캐시는 프로세스 전역이라 테스트 간 격리한다."""
    app_settings._cache.clear()
    yield
    app_settings._cache.clear()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestSettingsApi:
    async def test_catalog_masks_secrets(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        resp = await test_client.get("/api/v1/admin/settings", headers=_auth(super_admin_token))
        assert resp.status_code == 200, resp.text
        items = {i["key"]: i for i in resp.json()["items"]}
        # 시크릿은 값이 절대 안 나온다
        assert items["anthropic_api_key"]["is_secret"] is True
        assert items["anthropic_api_key"]["value"] is None
        # 비밀 아닌 값은 노출
        assert items["nw_client_id"]["is_secret"] is False

    async def test_set_secret_encrypts_and_masks(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        resp = await test_client.put(
            "/api/v1/admin/settings/anthropic_api_key",
            headers=_auth(super_admin_token),
            json={"value": "sk-test-secret-123"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is True
        assert body["source"] == "db"
        assert body["value"] is None  # 마스킹
        # 유효값(복호화)이 실제로 반영됐는지 — 소비자가 읽는 경로
        assert app_settings.get_str("anthropic_api_key") == "sk-test-secret-123"

    async def test_set_nonsecret_visible_and_effective(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        resp = await test_client.put(
            "/api/v1/admin/settings/nw_client_id",
            headers=_auth(super_admin_token),
            json={"value": "WORKS-CLIENT-XYZ"},
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "WORKS-CLIENT-XYZ"
        assert app_settings.get_str("nw_client_id") == "WORKS-CLIENT-XYZ"

    async def test_clear_reverts_to_env(
        self, test_client: AsyncClient, super_admin_token: str
    ) -> None:
        await test_client.put(
            "/api/v1/admin/settings/nw_service_account",
            headers=_auth(super_admin_token),
            json={"value": "svc@works"},
        )
        assert app_settings.is_overridden("nw_service_account") is True
        # 빈 값 → 오버라이드 해제
        cleared = await test_client.put(
            "/api/v1/admin/settings/nw_service_account",
            headers=_auth(super_admin_token),
            json={"value": ""},
        )
        assert cleared.status_code == 200
        assert app_settings.is_overridden("nw_service_account") is False

    async def test_admin_forbidden(self, test_client: AsyncClient, admin_token: str) -> None:
        # super_admin 전용 — 일반 admin은 403
        resp = await test_client.get("/api/v1/admin/settings", headers=_auth(admin_token))
        assert resp.status_code == 403

    async def test_unknown_key_404(self, test_client: AsyncClient, super_admin_token: str) -> None:
        resp = await test_client.put(
            "/api/v1/admin/settings/nonexistent_key",
            headers=_auth(super_admin_token),
            json={"value": "x"},
        )
        assert resp.status_code == 404
