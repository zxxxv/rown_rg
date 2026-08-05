from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from src.clients.llm import token_tracker
from src.clients.llm.token_usage_retry_worker import run_retry_cycle
from src.core.config import settings
from src.db.models.token_usage import TokenUsage
from src.db.models.token_usage_retry import TokenUsageRetry
from src.db.models.user import User


async def _all_token_usage(session: AsyncSession) -> list[TokenUsage]:
    result = await session.execute(select(TokenUsage))
    return list(result.scalars())


async def _all_retry_rows(session: AsyncSession) -> list[TokenUsageRetry]:
    result = await session.execute(select(TokenUsageRetry))
    return list(result.scalars())


async def _record(worker_user: User, **overrides: object) -> None:
    kwargs: dict[str, object] = {
        "model": "claude-opus-4-7",
        "input_tokens": 10,
        "output_tokens": 5,
        "cached_input_tokens": 0,
        "cost_usd": Decimal("0.01"),
        "mode": "live",
    }
    kwargs.update(overrides)
    async with token_tracker.token_context(
        user_id=worker_user.id, project_id=None, operation="test_op"
    ):
        await token_tracker.record_usage_safe(**kwargs)  # type: ignore[arg-type]


class TestRecordUsageSafeSuccess:
    async def test_successful_write_creates_no_retry_row(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        await _record(worker_user)

        usages = await _all_token_usage(test_session)
        retries = await _all_retry_rows(test_session)
        assert len(usages) == 1
        assert usages[0].model == "claude-opus-4-7"
        assert retries == []


class TestRecordUsageSafeEnqueuesOnFailure:
    async def test_db_failure_enqueues_retry_row(
        self, test_session: AsyncSession, worker_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(**kwargs: object) -> None:
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(token_tracker, "record_usage", _boom)

        await _record(worker_user)

        usages = await _all_token_usage(test_session)
        retries = await _all_retry_rows(test_session)
        assert usages == []
        assert len(retries) == 1
        row = retries[0]
        assert row.status == "pending"
        assert row.attempt_count == 0
        assert row.payload["user_id"] == str(worker_user.id)
        assert row.payload["model"] == "claude-opus-4-7"
        assert row.payload["cost_usd"] == "0.01"
        assert "db unavailable" in (row.last_error or "")


class TestRetryQueueReprocessing:
    async def test_pending_item_is_reprocessed_successfully(
        self, test_session: AsyncSession, worker_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_record_usage = token_tracker.record_usage
        should_fail = {"value": True}

        async def _flaky(**kwargs: object) -> None:
            if should_fail["value"]:
                raise RuntimeError("db unavailable")
            await original_record_usage(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(token_tracker, "record_usage", _flaky)

        await _record(worker_user)
        should_fail["value"] = False

        processed = await run_retry_cycle()

        usages = await _all_token_usage(test_session)
        retries = await _all_retry_rows(test_session)
        assert processed == 1
        assert len(usages) == 1
        assert usages[0].model == "claude-opus-4-7"
        assert usages[0].user_id == worker_user.id
        assert len(retries) == 1
        assert retries[0].status == "succeeded"
        assert retries[0].attempt_count == 1
        assert retries[0].last_error is None


class TestRetryExhaustion:
    async def test_exceeding_max_attempts_marks_failed_and_stops_retrying(
        self, test_session: AsyncSession, worker_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "token_usage_retry_max_attempts", 2)
        monkeypatch.setattr(settings, "token_usage_retry_base_delay_seconds", 0.0)
        monkeypatch.setattr(settings, "token_usage_retry_max_delay_seconds", 0.0)

        async def _boom(**kwargs: object) -> None:
            raise RuntimeError("still down")

        monkeypatch.setattr(token_tracker, "record_usage", _boom)

        await _record(worker_user)

        processed_1 = await run_retry_cycle()
        processed_2 = await run_retry_cycle()
        processed_3 = await run_retry_cycle()

        retries = await _all_retry_rows(test_session)
        assert processed_1 == 1
        assert processed_2 == 1
        assert processed_3 == 0  # 이미 pending이 아니므로 스캔 대상에서 제외됨
        assert len(retries) == 1
        assert retries[0].status == "failed"
        assert retries[0].attempt_count == 2
        assert "still down" in (retries[0].last_error or "")

        usages = await _all_token_usage(test_session)
        assert usages == []


class TestRetryEnqueueFailureIsLogged:
    async def test_enqueue_failure_falls_back_to_exception_log(
        self, test_session: AsyncSession, worker_user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom_record(**kwargs: object) -> None:
            raise RuntimeError("db unavailable")

        async def _boom_enqueue(**kwargs: object) -> None:
            raise RuntimeError("outbox write also unavailable")

        monkeypatch.setattr(token_tracker, "record_usage", _boom_record)
        monkeypatch.setattr(token_tracker, "enqueue_retry", _boom_enqueue)

        with capture_logs() as logs:
            await _record(worker_user)

        assert any(log["event"] == "token_usage.retry_enqueue_failed" for log in logs)
        usages = await _all_token_usage(test_session)
        retries = await _all_retry_rows(test_session)
        assert usages == []
        assert retries == []
