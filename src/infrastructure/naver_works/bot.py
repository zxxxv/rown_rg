import logging
from urllib.parse import quote

import httpx

from src.core import app_settings
from src.infrastructure.naver_works.auth import NW_HTTP_TIMEOUT_S, get_valid_token

logger = logging.getLogger(__name__)

_NW_API_BASE = "https://www.worksapis.com/v1.0"


# 상황별 본문·버튼 문구 - 링크가 데려가는 화면과 말이 맞아야 한다(2026-08-14).
# success=개요(즉시 다운로드), partial=검토 화면(자료/본문), failed=개요(상태·재시작).
_BOT_MESSAGES = {
    "success": {
        "text": "✅ 보고서 작성이 완료되었습니다.\n결과를 확인하고 바로 다운로드할 수 있습니다.",
        "button": "보고서 확인하기",
    },
    # partial이 실제로 오는 경우는 자료 검토 게이트뿐이다 — 작성이 멈춘 게 아니라
    # 수집이 끝나 승인을 기다리는 상황(QA 게이트는 자동 채택으로 제거됨, 2026-08-12).
    "partial": {
        "text": "⚠️ 자료 수집이 끝났습니다.\n수집된 자료를 검토해 주시면 보고서 작성이 시작됩니다.",
        "button": "자료 검토하기",
    },
    "failed": {
        "text": (
            "❌ 보고서 작성 중 오류가 발생했습니다.\n프로젝트 상태를 확인하고 다시 시작해 주세요."
        ),
        "button": "프로젝트 확인하기",
    },
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

    message = _BOT_MESSAGES.get(result_type, _BOT_MESSAGES["success"])

    payload = {
        "content": {
            "type": "button_template",
            "contentText": f"안녕하세요, {user_name}님 👋\n\n{message['text']}",
            "actions": [
                {
                    "type": "uri",
                    "label": message["button"],
                    "uri": result_url,
                }
            ],
        }
    }

    try:
        async with httpx.AsyncClient(timeout=NW_HTTP_TIMEOUT_S) as client:
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
