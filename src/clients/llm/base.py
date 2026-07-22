from typing import Literal, Protocol

from pydantic import BaseModel, Field

LLMMode = Literal["live", "record", "replay"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class WebSearchConfig(BaseModel):
    """provider-중립 웹검색 능력 표현. 어댑터가 각자 provider 도구 형태로 번역한다."""

    max_uses: int = 5
    fetch_pages: bool = True  # 검색으로 찾은 페이지 본문까지 회수(web_fetch 류)할지
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    user_country: str | None = None  # 검색 지역화 힌트(ISO alpha-2), 예: "KR"


class WebSource(BaseModel):
    """어댑터가 provider 응답을 정규화한 웹 출처. 검색 결과 + 회수 본문을 합친다."""

    url: str
    title: str | None = None
    content_md: str | None = None  # 회수한 본문(있으면)
    snippet: str | None = None
    page_age: str | None = None


class CompletionRequest(BaseModel):
    messages: list[Message]
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7
    system: str | None = None
    # 설정 시 어댑터가 웹검색 도구를 켠다(provider-중립). 단발 호출엔 영향 없음.
    web_search: WebSearchConfig | None = None
    cache_key: str | None = Field(
        default=None,
        description="녹화·재생 시 캐셋 식별자. None이면 input_hash에서 자동 생성",
    )


class CompletionResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    model: str
    stop_reason: str
    # web_search 사용 시 어댑터가 provider 응답을 정규화해 채운다.
    web_sources: list[WebSource] = []


class LLMClient(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
