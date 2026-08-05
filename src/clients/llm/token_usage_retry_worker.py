"""token_usage_retry_queue 인프로세스 재처리 루프 (I/O 계층).

record_usage_safe가 outbox(token_usage_retry_queue)에 적재한 실패 payload를 주기적으로
재시도한다. 별도 브로커/워커 프로세스 없이 `src.workflows.runner`와 같은 스타일로
앱 프로세스 안에서 asyncio.create_task 기반 백그라운드 루프로 돈다(main.py lifespan에서
시작/종료).

재시도 간격은 항목별 attempt_count에 따른 지수 백오프(base_delay * 2**attempt_count,
max_delay로 상한)로 계산하며, 루프 자체의 스캔 주기(token_usage_retry_interval_seconds)와는
별개다 — 루프는 자주 돌되, 그중 백오프가 지난 항목만 실제로 재시도한다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select

from src.clients.llm import token_tracker
from src.core.clock import now as clock_now
from src.core.config import settings
from src.db.models.token_usage_retry import TokenUsageRetry
from src.db.session import async_session_maker

logger = structlog.get_logger(__name__)

# GC 방지를 위해 살아있는 백그라운드 태스크 참조를 유지한다 (workflows.runner._TASKS와 동일 패턴).
_LOOP_TASK: asyncio.Task[Any] | None = None


def _backoff_seconds(attempt_count: int) -> float:
    return float(
        min(
            settings.token_usage_retry_base_delay_seconds * (2**attempt_count),
            settings.token_usage_retry_max_delay_seconds,
        )
    )


def _is_eligible(attempt_count: int, last_attempted_at: datetime | None, at: datetime) -> bool:
    if last_attempted_at is None:
        return True
    return at >= last_attempted_at + timedelta(seconds=_backoff_seconds(attempt_count))


async def _reprocess_by_id(retry_id: uuid.UUID) -> None:
    """재시도 큐의 항목 하나를 자기 세션에서 처리·커밋한다(항목 간 실패 격리)."""
    async with async_session_maker() as session:
        row = await session.get(TokenUsageRetry, retry_id)
        if row is None or row.status != "pending":
            return

        payload = row.payload
        row.attempt_count += 1
        row.last_attempted_at = clock_now()

        try:
            user_id = uuid.UUID(payload["user_id"]) if payload.get("user_id") else None
            project_id = uuid.UUID(payload["project_id"]) if payload.get("project_id") else None
            async with token_tracker.token_context(
                user_id=user_id,
                project_id=project_id,
                operation=payload["operation"],
            ):
                await token_tracker.record_usage(
                    model=payload["model"],
                    input_tokens=payload["input_tokens"],
                    output_tokens=payload["output_tokens"],
                    cached_input_tokens=payload["cached_input_tokens"],
                    cost_usd=Decimal(payload["cost_usd"]),
                    mode=payload["mode"],
                )
        except Exception as exc:
            row.last_error = str(exc)[:4000]
            if row.attempt_count >= settings.token_usage_retry_max_attempts:
                row.status = "failed"
                logger.error(
                    "token_usage.retry_exhausted",
                    retry_id=str(row.id),
                    attempt_count=row.attempt_count,
                    payload=payload,
                    error=row.last_error,
                )
            else:
                logger.warning(
                    "token_usage.retry_attempt_failed",
                    retry_id=str(row.id),
                    attempt_count=row.attempt_count,
                    error=row.last_error,
                )
        else:
            row.status = "succeeded"
            row.last_error = None
            logger.info(
                "token_usage.retry_succeeded",
                retry_id=str(row.id),
                attempt_count=row.attempt_count,
            )

        await session.commit()


async def run_retry_cycle() -> int:
    """대기(pending) 중이며 백오프가 지난 항목을 한 번 훑어 재처리한다. 처리 건수 반환."""
    now_ts = clock_now()
    async with async_session_maker() as session:
        result = await session.execute(
            select(
                TokenUsageRetry.id,
                TokenUsageRetry.attempt_count,
                TokenUsageRetry.last_attempted_at,
            )
            .where(TokenUsageRetry.status == "pending")
            .order_by(TokenUsageRetry.created_at)
            .limit(200)
        )
        candidates = result.all()

    eligible_ids = [
        row.id
        for row in candidates
        if _is_eligible(row.attempt_count, row.last_attempted_at, now_ts)
    ]

    for retry_id in eligible_ids:
        await _reprocess_by_id(retry_id)

    return len(eligible_ids)


async def _loop() -> None:
    while True:
        try:
            processed = await run_retry_cycle()
            if processed:
                logger.info("token_usage.retry_cycle_done", processed=processed)
        except Exception:
            # 사이클 자체가 예기치 못하게 실패해도 루프는 죽지 않고 다음 주기에 재시도한다.
            logger.exception("token_usage.retry_cycle_failed")
        await asyncio.sleep(settings.token_usage_retry_interval_seconds)


def start_retry_loop() -> None:
    global _LOOP_TASK
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return
    _LOOP_TASK = asyncio.create_task(_loop())


async def stop_retry_loop() -> None:
    global _LOOP_TASK
    if _LOOP_TASK is None:
        return
    _LOOP_TASK.cancel()
    try:
        await _LOOP_TASK
    except asyncio.CancelledError:
        pass
    _LOOP_TASK = None
