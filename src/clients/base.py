from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

LLMMode = Literal["live", "record", "replay"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    messages: list[Message]
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7
    system: str | None = None
    cache_key: str | None = Field(
        None,
        description="녹화·재생 시 캐셋 식별자. None이면 input_hash에서 자동 생성",
    )


class CompletionResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    model: str
    stop_reason: str


@runtime_checkable
class LLMClient(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
