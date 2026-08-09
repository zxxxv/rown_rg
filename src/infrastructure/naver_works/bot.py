import logging
from urllib.parse import quote

import httpx

from src.core import app_settings
from src.infrastructure.naver_works.auth import get_valid_token

logger = logging.getLogger(__name__)

_NW_API_BASE = "https://www.worksapis.com/v1.0"


_BOT_MESSAGES = {
    "success": "✅ 요청하신 작업이 완료되었습니다.",
    "partial": "⚠️ 요청하신 작업이 일부만 완료되었습니다. 확인이 필요합니다.",
    "failed": "❌ 요청하신 작업 처리 중 오류가 발생했습니다.",
}


async def send_bot_message(
    target_email: str,
    user_name: str,
    result_url: str,
    result_type: str,
) -> None:
    token = await get_valid_token()
    user_id = quote(target_email, safe="@")
    url = f"{_NW_API_BASE}/bots/{app_settings.get_str('nw_bot_id')}/users/{user_id}/messages"

    message_body = _BOT_MESSAGES.get(result_type, _BOT_MESSAGES["success"])

    payload = {
        "content": {
            "type": "button_template",
            "contentText": (
                f"안녕하세요, {user_name}님 👋\n\n"
                f"{message_body}\n\n"
                f"아래 버튼을 눌러 결과를 확인해보세요!"
            ),
            "actions": [
                {
                    "type": "uri",
                    "label": "결과 확인하기",
                    "uri": result_url,
                }
            ],
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        logger.info("naver_works.bot.sent", extra={"target": target_email})

    except httpx.HTTPStatusError as e:
        logger.error(
            "naver_works.bot.failed",
            extra={
                "target": target_email,
                "status": e.response.status_code,
                "body": e.response.text,
            },
        )
        raise
    except httpx.RequestError as e:
        logger.error(
            "naver_works.bot.request_error",
            extra={"target": target_email, "error": str(e)},
        )
        raise
