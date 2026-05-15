import asyncio
from typing import Any

import structlog
from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from src.clients import token_tracker
from src.clients.base import CompletionRequest, CompletionResponse, LLMMode
from src.clients.cassette_manager import (
    CassetteManager,
    compute_input_hash,
    resolve_cache_key,
)
from src.clients.cost_calculator import CostCalculator
from src.clients.exceptions import (
    CassetteNotFoundError,
    LLMAPIError,
    LLMRateLimitError,
)

logger = structlog.get_logger(__name__)


class AnthropicAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        mode: LLMMode,
        cassette_manager: CassetteManager,
        max_retries: int = 3,
        base_retry_delay: float = 1.0,
        allow_replay_fallback: bool = False,
    ):
        self.mode = mode
        self.cassette_manager = cassette_manager
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.allow_replay_fallback = allow_replay_fallback
        self._client: AsyncAnthropic | None = (
            AsyncAnthropic(api_key=api_key) if mode != "replay" else None
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        operation = token_tracker.get_operation() or "unknown"
        input_hash = compute_input_hash(request)
        cache_key = resolve_cache_key(request, input_hash)

        effective_mode: LLMMode = self.mode
        if effective_mode == "replay" and self.allow_replay_fallback:
            if not self.cassette_manager.exists(operation, cache_key, input_hash):
                logger.info(
                    "llm.cassette.miss",
                    operation=operation,
                    cache_key=cache_key,
                    fallback="record",
                )
                effective_mode = "record"
                if self._client is None:
                    self._ensure_client_for_fallback()

        if effective_mode == "replay":
            response = self.cassette_manager.load(operation, cache_key, input_hash)
        else:
            response = await self._call_with_retry(request)
            if effective_mode == "record":
                await self.cassette_manager.save(
                    operation, cache_key, input_hash, request, response
                )

        cost = CostCalculator.calculate(
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_input_tokens=response.cached_input_tokens,
        )
        asyncio.create_task(
            token_tracker.record_usage_safe(
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cached_input_tokens=response.cached_input_tokens,
                cost_usd=cost,
                mode=effective_mode,
            )
        )
        return response

    def _ensure_client_for_fallback(self) -> None:
        raise CassetteNotFoundError(
            "Replay fallback requires AsyncAnthropic client; instantiate adapter "
            "with allow_replay_fallback=True only when api_key is configured.",
        )

    async def _call_with_retry(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None
        delay = self.base_retry_delay
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._call_anthropic(request)
            except RateLimitError as e:
                last_err = e
                logger.warning(
                    "llm.retry.rate_limited",
                    attempt=attempt,
                    max_retries=self.max_retries,
                    delay_sec=delay,
                    provider="anthropic",
                    model=request.model,
                )
            except APIConnectionError as e:
                last_err = e
                logger.warning(
                    "llm.retry.connection_error",
                    attempt=attempt,
                    max_retries=self.max_retries,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    provider="anthropic",
                    model=request.model,
                )
            except APIStatusError as e:
                if e.status_code < 500:
                    raise LLMAPIError(f"Anthropic API error {e.status_code}: {e}") from e
                last_err = e
                logger.warning(
                    "llm.retry.server_error",
                    status_code=e.status_code,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    provider="anthropic",
                    model=request.model,
                )
            except APIError as e:
                last_err = e
                logger.warning(
                    "llm.retry.api_error",
                    attempt=attempt,
                    max_retries=self.max_retries,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    provider="anthropic",
                    model=request.model,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(delay)
                delay *= 2

        if isinstance(last_err, RateLimitError):
            raise LLMRateLimitError(str(last_err)) from last_err
        raise LLMAPIError(str(last_err)) from last_err

    async def _call_anthropic(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None
        anth_messages = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": anth_messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.system:
            kwargs["system"] = request.system

        result = await self._client.messages.create(**kwargs)
        text_blocks = [getattr(b, "text", "") for b in result.content]
        return CompletionResponse(
            content="".join(text_blocks),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_input_tokens=getattr(result.usage, "cache_read_input_tokens", 0) or 0,
            model=result.model,
            stop_reason=result.stop_reason or "end_turn",
        )
