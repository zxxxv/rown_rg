from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from src.clients.llm.adapters.base import BaseLLMAdapter, RetryKind
from src.clients.llm.base import CompletionRequest, CompletionResponse


class AnthropicAdapter(BaseLLMAdapter):
    provider = "anthropic"

    def _create_client(self, api_key: str) -> Any:
        return AsyncAnthropic(api_key=api_key)

    def _classify_error(self, exc: Exception) -> RetryKind | None:
        if isinstance(exc, RateLimitError):
            return "rate_limit"
        if isinstance(exc, APIConnectionError):
            return "retryable"
        if isinstance(exc, APIStatusError):
            return "fatal" if exc.status_code < 500 else "retryable"
        if isinstance(exc, APIError):
            return "retryable"
        return None

    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None
        # Anthropic Messages API: system은 messages가 아니라 별도 파라미터
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
