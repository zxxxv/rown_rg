import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

import structlog

from src.clients.llm.base import LLMMode
from src.db.models.token_usage import TokenUsage
from src.db.session import async_session_maker

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
    async with async_session_maker() as session:
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
