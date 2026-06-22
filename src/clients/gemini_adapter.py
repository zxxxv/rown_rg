from typing import Any

from src.clients.base import CompletionRequest, CompletionResponse
from src.clients.base_adapter import BaseLLMAdapter, RetryKind


class GeminiAdapter(BaseLLMAdapter):
    """
    Google Gemini

    BaseLLMAdapter의 공통 로직(캐셋·재시도·비용추적)은 상속받아 그대로 동작하고,
    아래 provider별 3개 메서드만 채우면 됨:
      - _create_client : google-genai Client 생성 (lazy import)
      - _call_provider : generate_content 호출 + 응답 변환
      - _classify_error: google.genai.errors > 재시도 분류
    """

    provider = "gemini"

    def _create_client(self, api_key: str) -> Any:
        raise NotImplementedError("GeminiAdapter._create_client 구현 예정")

    def _classify_error(self, exc: Exception) -> RetryKind | None:
        raise NotImplementedError("GeminiAdapter._classify_error 구현 예정")

    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError("GeminiAdapter._call_provider 구현 예정")
