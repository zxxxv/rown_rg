import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_async_session
from src.api.dependencies.internal_auth import verify_internal_api_key
from src.api.schemas.notify import NotifyRequest
from src.db.models.user import User
from src.infrastructure.naver_works.bot import send_bot_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notify", tags=["notify"])


async def _dispatch_notifications(
    target_email: str,
    user_name: str,
    result_url: str,
    result_type: str,
) -> None:
    try:
        await send_bot_message(
            target_email=target_email,
            user_name=user_name,
            result_url=result_url,
            result_type=result_type,
        )
    except Exception:
        logger.exception("notify.bot.failed", extra={"target": target_email})

    # try:
    #    await send_mail(
    #        target_email=target_email,
    #        user_name=user_name,
    #        result_url=result_url,
    #        result_type=result_type,
    #    )
    # except Exception:
    #    logger.exception("notify.mail.failed", extra={"target": target_email})


@router.post("/llm-complete", status_code=status.HTTP_202_ACCEPTED)
async def notify_llm_complete(
    payload: NotifyRequest,
    bg: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(verify_internal_api_key),
) -> dict:
    result = await session.execute(
        select(User).where(
            User.email == payload.target_email,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "USER_NOT_FOUND",
                "message": "대상 사용자를 찾을 수 없습니다.",
            },
        )

    bg.add_task(
        _dispatch_notifications,
        target_email=user.email,
        user_name=user.name,
        result_url=payload.result_url,
        result_type=payload.result_type.value,
    )
    return {"status": "accepted"}
