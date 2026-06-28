import logging
import secrets

from fastapi import Header, HTTPException, status

from src.core.config import settings

logger = logging.getLogger(__name__)


async def verify_internal_api_key(
    x_internal_api_key: str = Header(..., alias="X-Internal-API-Key"),
) -> None:
    is_valid = secrets.compare_digest(
        x_internal_api_key.encode(),
        settings.internal_api_key.encode(),
    )
    if not is_valid:
        logger.warning("internal_auth.invalid_key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_INTERNAL_KEY", "message": "Unauthorized"},
        )
