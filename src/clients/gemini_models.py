from typing import Final, Literal

from src.clients.base import CompletionRequest

GeminiModel = Literal[
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

DEFAULT_GEMINI_MODEL: Final[GeminiModel] = "gemini-2.5-flash-lite"

GEMINI_MODELS: Final[tuple[GeminiModel, ...]] = (
    DEFAULT_GEMINI_MODEL,
    "gemini-2.5-flash",
)

SUPPORTED_GEMINI_MODELS: Final[frozenset[str]] = frozenset(GEMINI_MODELS)


class GeminiCompletionRequest(CompletionRequest):
    """Gemini 모델 미지정 시 최저가 모델을 사용한다."""

    model: GeminiModel = DEFAULT_GEMINI_MODEL
