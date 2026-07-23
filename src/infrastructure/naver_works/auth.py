import asyncio
import logging
import time

import httpx
import jwt

from src.core import app_settings
from src.core.config import settings

logger = logging.getLogger(__name__)

_NW_TOKEN_URL = "https://auth.worksmobile.com/oauth2/v2.0/token"

_cache: dict = {
    "access_token": None,
    "expires_at": 0.0,
}
_lock = asyncio.Lock()


def _build_jwt_assertion() -> str:
    now = int(time.time())
    payload = {
        "iss": app_settings.get_str("nw_client_id"),
        "sub": app_settings.get_str("nw_service_account"),
        "iat": now,
        "exp": now + settings.nw_token_expire_sec,
    }
    # env는 개행을 \n 이스케이프로 저장 → 실개행 복원(DB의 실개행 PEM은 그대로 통과).
    private_key_pem = app_settings.get_str("nw_private_key").replace("\\n", "\n")
    return jwt.encode(
        payload,
        private_key_pem,
        algorithm="RS256",
    )


async def _issue_access_token() -> str:
    assertion = _build_jwt_assertion()

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _NW_TOKEN_URL,
            data={
                "assertion": assertion,
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "client_id": app_settings.get_str("nw_client_id"),
                "client_secret": app_settings.get_str("nw_client_secret"),
                "scope": "bot bot.message",
            },
        )
        resp.raise_for_status()

    token = resp.json()["access_token"]
    logger.info("naver_works.token.issued")
    return token


async def get_valid_token() -> str:
    async with _lock:
        now = time.time()
        if (
            _cache["access_token"] is not None
            and now < _cache["expires_at"] - settings.nw_refresh_buffer
        ):
            return _cache["access_token"]

        new_token = await _issue_access_token()
        _cache["access_token"] = new_token
        _cache["expires_at"] = now + settings.nw_token_expire_sec
        return new_token
