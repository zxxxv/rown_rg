from typing import Any

from src.clients.llm.adapters.base import BaseLLMAdapter, RetryKind
from src.clients.llm.base import CompletionRequest, CompletionResponse


class OpenAIAdapter(BaseLLMAdapter):
    """
    OpenAI(GPT)

    BaseLLMAdapter의 공통 로직(캐셋·재시도·비용추적)은 상속받아 그대로 동작하고,
    아래 provider별 3개 메서드만 채우면 됨:
      - _create_client : openai.AsyncOpenAI 생성 (lazy import)
      - _call_provider : chat.completions.create 호출 + 응답 변환
      - _classify_error: openai 예외 > 재시도 분류
    """

    provider = "openai"

    def _create_client(self, api_key: str) -> Any:
        raise NotImplementedError("OpenAIAdapter._create_client 구현 예정")

    def _classify_error(self, exc: Exception) -> RetryKind | None:
        raise NotImplementedError("OpenAIAdapter._classify_error 구현 예정")

    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        if request.web_search is not None:
            # 인터페이스는 중립이지만 OpenAI 측 웹검색(Responses API 등) 번역·정규화는 미구현.
            raise NotImplementedError("web_search not supported for openai yet")
        raise NotImplementedError("OpenAIAdapter._call_provider 구현 예정")
