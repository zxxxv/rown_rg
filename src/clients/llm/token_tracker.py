import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

import sentry_sdk
import structlog
from sqlalchemy import func, select

from src.clients.llm.base import LLMMode
from src.db.models.token_usage import TokenUsage
from src.db.session import open_session
from src.workflows.events import emit_cost

logger = structlog.get_logger(__name__)

_user_id: ContextVar[uuid.UUID | None] = ContextVar("llm_user_id", default=None)
_project_id: ContextVar[uuid.UUID | None] = ContextVar("llm_project_id", default=None)
_operation: ContextVar[str | None] = ContextVar("llm_operation", default=None)


@contextmanager
def token_context(
    *,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    operation: str,
) -> Iterator[None]:
    tokens = (
        _user_id.set(user_id),
        _project_id.set(project_id),
        _operation.set(operation),
    )
    try:
        yield
    finally:
        _user_id.reset(tokens[0])
        _project_id.reset(tokens[1])
        _operation.reset(tokens[2])


def get_operation() -> str | None:
    return _operation.get()


def get_context() -> tuple[uuid.UUID | None, uuid.UUID | None, str | None]:
    return _user_id.get(), _project_id.get(), _operation.get()


async def record_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    cost_usd: Decimal,
    mode: LLMMode,
) -> None:
    user_id, project_id, operation = get_context()
    async with open_session() as session:
        usage = TokenUsage(
            user_id=user_id,
            project_id=project_id,
            model=model,
            operation=operation or "unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost_usd,
            mode=mode,
        )
        session.add(usage)
        await session.commit()

        if project_id is not None:
            totals = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0
                        ),
                        func.coalesce(func.sum(TokenUsage.cost_usd), 0),
                    ).where(TokenUsage.project_id == project_id)
                )
            ).one()
            emit_cost(project_id, int(totals[0]), float(totals[1]))


async def record_usage_safe(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    cost_usd: Decimal,
    mode: LLMMode,
) -> None:
    try:
        await record_usage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost_usd,
            mode=mode,
        )
    except Exception:
        user_id, project_id, _ = get_context()
        logger.exception(
            "token_usage.record_failed",
            user_id=str(user_id) if user_id else None,
            project_id=str(project_id) if project_id else None,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        # 과금 원장이 유실되는 지점 — 조용히 실패하면 정산이 틀어진다. 어떤 기록이
        # 빠졌는지 나중에 재구성할 수 있을 만큼만 싣는다: 모델·모드·토큰 수.
        # 금액(cost_usd)은 넣지 않는다. 키 이름에 "token"이 들어가면 before_send의
        # deny-list에 걸려 [Filtered]가 되므로 usage dict로 감싼다.
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("bg_failure", "token_usage_record")
            scope.set_tag("llm_model", model)
            scope.set_tag("llm_mode", mode)
            scope.set_extra(
                "usage",
                {
                    "input": input_tokens,
                    "output": output_tokens,
                    "cached_input": cached_input_tokens,
                },
            )
            scope.set_extra("project_id", str(project_id) if project_id else None)
            sentry_sdk.capture_exception()
