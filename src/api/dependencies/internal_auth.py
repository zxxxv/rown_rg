import logging
import secrets

from fastapi import Header, HTTPException, status

from src.core.config import settings

logger = logging.getLogger(__name__)


async def verify_internal_api_key(
    x_internal_api_key: str = Header(..., alias="X-Internal-API-Key"),
) -> None:
    expected = settings.internal_api_key
    # 키 미설정 시 fail-closed. compare_digest("","")는 True라, 빈 키면 아무 요청이나
    # (빈 헤더 포함) 통과하게 된다 — 내부 엔드포인트가 무방비로 열리는 것을 막는다.
    if not expected:
        logger.error("internal_auth.key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_INTERNAL_KEY", "message": "Unauthorized"},
        )
    is_valid = secrets.compare_digest(
        x_internal_api_key.encode(),
        expected.encode(),
    )
    if not is_valid:
        logger.warning("internal_auth.invalid_key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_INTERNAL_KEY", "message": "Unauthorized"},
        )
