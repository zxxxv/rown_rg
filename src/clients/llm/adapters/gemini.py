from typing import Any, Final

import httpx

from src.clients.llm.adapters.base import BaseLLMAdapter, RetryKind
from src.clients.llm.base import CompletionRequest, CompletionResponse, Message
from src.clients.llm.exceptions import LLMAPIError
from src.clients.llm.models import supported_ids
from src.core.config import settings

SUPPORTED_GEMINI_MODELS: Final[frozenset[str]] = supported_ids("gemini")


class GeminiAdapter(BaseLLMAdapter):
    provider = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _create_client(self, api_key: str) -> Any:
        if not api_key:
            raise LLMAPIError("GEMINI_API_KEY가 설정되지 않았습니다.")
        # 60초 인라인은 장문 생성(RAPTOR·용어집)에서 먼저 터진다 - 다른 어댑터와
        # 같은 설정 키를 쓴다(2026-09-03 감사: config 우회 유일 사례였음).
        return httpx.AsyncClient(timeout=settings.llm_client_timeout_s)

    def _classify_error(self, exc: Exception) -> RetryKind | None:
        if isinstance(exc, httpx.RequestError):
            return "retryable"
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            if status_code == 429:
                return "rate_limit"
            if status_code >= 500:
                return "retryable"
            return "fatal"
        return None

    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None

        if request.web_search is not None:
            raise NotImplementedError("web_search not supported for gemini yet")

        if request.model not in SUPPORTED_GEMINI_MODELS:
            supported = ", ".join(sorted(SUPPORTED_GEMINI_MODELS))
            raise LLMAPIError(
                f"지원하지 않는 Gemini 모델입니다: {request.model}. 지원 모델: {supported}"
            )

        url = f"{self.base_url}/models/{request.model}:generateContent"

        response = await self._client.post(
            url,
            headers={
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            json=self._build_payload(request),
        )
        response.raise_for_status()

        return self._parse_response(response.json(), fallback_model=request.model)

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": [
                self._to_gemini_content(message)
                for message in request.messages
                if message.role != "system"
            ],
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }

        system_text = self._system_text(request)

        if system_text:
            payload["systemInstruction"] = {
                "parts": [
                    {
                        "text": system_text,
                    }
                ]
            }

        return payload

    def _system_text(self, request: CompletionRequest) -> str:
        system_messages = [
            message.content for message in request.messages if message.role == "system"
        ]

        if request.system:
            system_messages.insert(0, request.system)

        return "\n\n".join(system_messages).strip()

    def _to_gemini_content(self, message: Message) -> dict[str, Any]:
        role = "model" if message.role == "assistant" else "user"

        return {
            "role": role,
            "parts": [
                {
                    "text": message.content,
                }
            ],
        }

    def _parse_response(
        self,
        data: dict[str, Any],
        *,
        fallback_model: str,
    ) -> CompletionResponse:
        candidates = data.get("candidates") or []

        if not candidates:
            raise LLMAPIError(f"Gemini 응답에 candidates가 없습니다: {data}")

        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts") or []

        content = "".join(part.get("text", "") for part in parts if isinstance(part, dict))

        # Gemini token usage mapping:
        # - promptTokenCount -> input_tokens
        # - candidatesTokenCount -> generated output tokens
        # - thoughtsTokenCount -> thinking tokens, billed as output tokens
        # - cachedContentTokenCount -> cached_input_tokens
        #
        # Smoke test example:
        # - model: gemini-2.5-flash
        # - input_tokens: 6
        # - output_tokens: 124
        # - cached_input_tokens: 0

        usage = data.get("usageMetadata") or {}

        input_tokens = usage.get("promptTokenCount", 0) or 0
        candidate_tokens = usage.get("candidatesTokenCount", 0) or 0
        thinking_tokens = usage.get("thoughtsTokenCount", 0) or 0
        cached_tokens = usage.get("cachedContentTokenCount", 0) or 0

        return CompletionResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=candidate_tokens + thinking_tokens,
            cached_input_tokens=cached_tokens,
            model=fallback_model,
            stop_reason=candidate.get("finishReason") or "STOP",
        )
