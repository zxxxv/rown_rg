from typing import Literal

from src.clients.base import CompletionRequest

OpenAIModel = Literal[
    "gpt-5.4-nano",
    "gpt-5.4-mini",
]

DEFAULT_OPENAI_MODEL: OpenAIModel = "gpt-5.4-nano"

SUPPORTED_OPENAI_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5.4-nano",
        "gpt-5.4-mini",
    }
)


class OpenAICompletionRequest(CompletionRequest):
    """OpenAI 모델 미지정 시 저비용 기본모델 사용."""

    model: OpenAIModel = DEFAULT_OPENAI_MODEL
