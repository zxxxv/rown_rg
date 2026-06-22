from collections.abc import Callable

from src.clients.base import CompletionRequest, CompletionResponse, LLMClient
from src.clients.exceptions import LLMAPIError

PROVIDER_PREFIXES: dict[str, str] = {
    "claude": "anthropic",
    "gemini": "gemini",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
}


def resolve_provider(model: str) -> str:
    for prefix, provider in PROVIDER_PREFIXES.items():
        if model.startswith(prefix):
            return provider
    raise LLMAPIError(f"모델의 provider를 판별할 수 없습니다: {model}")


class LLMRouter:
    """
    모델 ID로 provider를 골라 해당 어댑터에 위임하는 LLMClient
    어댑터는 처음 쓰일 때 lazy 생성(builder 호출)
    """

    def __init__(self, builders: dict[str, Callable[[], LLMClient]]):
        self._builders = builders
        self._adapters: dict[str, LLMClient] = {}

    def _get(self, provider: str) -> LLMClient:
        adapter = self._adapters.get(provider)
        if adapter is None:
            builder = self._builders.get(provider)
            if builder is None:
                raise LLMAPIError(f"등록되지 않은 provider입니다: {provider}")
            adapter = builder()
            self._adapters[provider] = adapter
        return adapter

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        provider = resolve_provider(request.model)
        return await self._get(provider).complete(request)
