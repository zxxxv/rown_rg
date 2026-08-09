from typing import Any, Final

import httpx

from src.clients.llm.adapters.base import BaseLLMAdapter, RetryKind
from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.clients.llm.exceptions import LLMAPIError
from src.clients.llm.models import supported_ids
from src.core.config import settings

# 지원목록은 카탈로그(models.py)에서 파생 — 단일 진실. 기본 모델은 models.default_id("openai").
SUPPORTED_OPENAI_MODELS: Final[frozenset[str]] = supported_ids("openai")

# temperature를 받지 않는 추론 모델 접두사(gpt-5 계열·o 시리즈).
_NO_TEMPERATURE_PREFIXES: Final[tuple[str, ...]] = ("gpt-5", "o3", "o4")


def _accepts_temperature(model: str) -> bool:
    return not model.startswith(_NO_TEMPERATURE_PREFIXES)


class OpenAIAdapter(BaseLLMAdapter):
    """OpenAI(GPT) — Responses API(/v1/responses) 기반."""

    provider = "openai"
    base_url = "https://api.openai.com/v1"

    def _create_client(self, api_key: str) -> Any:
        if not api_key:
            raise LLMAPIError("OPENAI_API_KEY가 설정되지 않았습니다.")
        # 60초 고정은 장문 생성(파트당 4~6천자 + 추론 토큰)에서 먼저 터진다 —
        # Anthropic과 같은 설정값을 쓴다(2026-08-08).
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
            raise NotImplementedError("web_search not supported for openai yet")

        if request.model not in SUPPORTED_OPENAI_MODELS:
            supported = ", ".join(sorted(SUPPORTED_OPENAI_MODELS))
            raise LLMAPIError(
                f"지원하지 않는 OpenAI 모델입니다: {request.model}. 지원 모델: {supported}"
            )

        response = await self._client.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=self._build_payload(request),
        )
        response.raise_for_status()

        return self._parse_response(response.json(), fallback_model=request.model)

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "input": [
                {"role": message.role, "content": message.content}
                for message in request.messages
                if message.role != "system"
            ],
            "max_output_tokens": request.max_tokens,
        }
        # 추론 모델(gpt-5 계열)은 temperature 지정을 400으로 거부한다 —
        # Anthropic Opus에서 겪은 것과 같은 함정. 지원 모델에만 싣는다.
        if _accepts_temperature(request.model):
            payload["temperature"] = request.temperature
        else:
            # 추론 토큰이 max_output_tokens를 먹어 본문이 비는 것을 막는다
            # (어댑터가 '텍스트 출력 없음'으로 실패). 장문 작성엔 추론이 얕아도 된다.
            payload["reasoning"] = {"effort": "low"}

        system_text = self._system_text(request)
        if system_text:
            payload["instructions"] = system_text

        return payload

    def _system_text(self, request: CompletionRequest) -> str:
        system_messages = [
            message.content for message in request.messages if message.role == "system"
        ]
        if request.system:
            system_messages.insert(0, request.system)
        return "\n\n".join(system_messages).strip()

    def _parse_response(
        self,
        data: dict[str, Any],
        *,
        fallback_model: str,
    ) -> CompletionResponse:
        if data.get("error"):
            raise LLMAPIError(f"OpenAI 응답 오류: {data['error']}")

        text_parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    text_parts.append(part.get("refusal", ""))

        content = "".join(text_parts)
        if not content:
            raise LLMAPIError(f"OpenAI 응답에 텍스트 출력이 없습니다: {data}")

        usage = data.get("usage") or {}
        input_token_details = usage.get("input_tokens_details") or {}

        status = data.get("status") or "completed"
        if status == "incomplete":
            incomplete_details = data.get("incomplete_details") or {}
            stop_reason = incomplete_details.get("reason") or status
        else:
            stop_reason = status

        return CompletionResponse(
            content=content,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cached_input_tokens=(input_token_details.get("cached_tokens", 0) or 0),
            model=data.get("model") or fallback_model,
            stop_reason=stop_reason,
        )
