import logging
import secrets

import sentry_sdk
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
        # 배포 설정 사고 — 내부 엔드포인트가 통째로 fail-closed로 막힌다.
        # HTTPException(401)은 ASGI 통합이 안 잡고 logger.error도 event_level=
        # CRITICAL이라 이벤트를 안 만드니, 여기가 유일한 통로다.
        # 예외가 없는 상태 점검이라 capture_message를 쓴다. 키 **값**은 물론
        # 길이·접두사 같은 어떤 힌트도 싣지 않는다 — 미설정 사실만.
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("error_type", "config")
            sentry_sdk.capture_message(
                "internal API key is not configured; internal endpoints are refusing every request",
                level="error",
            )
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
