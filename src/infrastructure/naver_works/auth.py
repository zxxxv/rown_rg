import asyncio
import logging
import time

import httpx
import jwt

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
        "iss": settings.nw_client_id,
        "sub": settings.nw_service_account,
        "iat": now,
        "exp": now + settings.nw_token_expire_sec,
    }
    return jwt.encode(
        payload,
        settings.nw_private_key_pem,
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
                "client_id": settings.nw_client_id,
                "client_secret": settings.nw_client_secret,
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
