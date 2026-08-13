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
    # 회수(web_fetch) 전용 사용 횟수 — None이면 max_uses를 따른다.
    # 검색은 PDF를 끌어오지 않고 회수만 끌어온다. Anthropic은 요청당 PDF 100페이지가
    # 상한인데 max_content_tokens는 "binary content such as PDFs"에 적용되지 않아
    # (SDK 타입 주석) 회수 횟수가 유일한 제동 장치다 — 검색 폭은 유지하고 회수만 조인다.
    max_fetch_uses: int | None = None
    # 회수 페이지당 본문 토큰 상한 — pause_turn 루프가 회수 전문을 대화에 누적시키므로
    # 상한이 없으면 큰 페이지 몇 개로 컨텍스트 한도(200k)를 뚫는다(2026-08-03 실측 1.35M).
    # 주의: PDF에는 적용되지 않는다(위 max_fetch_uses 참고).
    max_content_tokens: int = 20_000
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
    # 추론(thinking) 깊이 힌트("low"|"medium"|"high"). thinking이 기본 켜지는 모델
    # (Opus 5 등)에서 절 작성처럼 근거가 프롬프트에 다 있는 생성 작업의 추론 과금을
    # 줄이는 레버다 — Anthropic 어댑터가 output_config.effort로 번역하고, 지원하지
    # 않는 모델·provider는 조용히 무시한다. None이면 파라미터 자체를 보내지 않는다.
    effort: str | None = None
    cache_key: str | None = Field(
        default=None,
        description="녹화·재생 시 캐셋 식별자. None이면 input_hash에서 자동 생성",
    )
    # 프롬프트 캐싱 힌트: 앞에서부터 N개 메시지가 연속 호출 간 공유되는 프리픽스임을
    # 어댑터에 알린다(분할 생성의 파트 순차 호출 등). Anthropic 어댑터가 그 경계에
    # 캐시 브레이크포인트를 찍어 두 번째 호출부터 프리픽스를 0.1배로 읽는다.
    # 전송 내용 자체는 동일하므로 카세트 input_hash에서는 제외된다(cassette.py).
    cache_prefix_messages: int = 0


class CompletionResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0  # 캐시에서 읽은 입력(0.1배 과금) — input_tokens에 미포함
    # 캐시에 새로 쓴 입력(Anthropic 1.25배 과금) — input_tokens에 미포함이라 이 필드를
    # 버리면 캐싱 활성화 순간 비용·한도 계정이 과소계상된다. 타 provider는 0.
    cache_write_input_tokens: int = 0
    model: str
    stop_reason: str
    # web_search 사용 시 어댑터가 provider 응답을 정규화해 채운다.
    web_sources: list[WebSource] = []


# 정상 완결로 인정하는 stop_reason — provider별 표기를 한곳에 모은다.
# Anthropic: end_turn·stop_sequence / OpenAI(Responses): completed / Gemini: STOP /
# 테스트 fake: stop. 화이트리스트 방식이라 미지의 값(max_tokens·refusal·SAFETY·
# max_output_tokens 등)은 전부 미완결로 분류된다 — 절단이 조용히 통과하는 것보다
# 재시도가 낫다(2026-08-13 실사고: 문장 중간 216자 토막이 완성 절로 조립됨).
COMPLETE_STOP_REASONS: frozenset[str] = frozenset(
    {"end_turn", "stop_sequence", "stop", "completed", "STOP"}
)


def incomplete_stop(stop_reason: str) -> str:
    """미완결이면 그 이유(stop_reason)를, 정상 완결이면 빈 문자열을 돌려준다."""
    return "" if stop_reason in COMPLETE_STOP_REASONS else stop_reason


class LLMClient(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
