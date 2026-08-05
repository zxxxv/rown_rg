"""provider별 모델 카탈로그

여기 한 곳만 고치면 라우팅(router)·비용(cost)·지원목록(adapters)이 모두 따라온다.
- 새 모델 추가: MODELS에 ModelSpec 한 줄
- 단가 미책정(pricing=None) 모델은 비용 0으로 처리된다(BaseLLMAdapter._safe_cost)

OpenAI 모델은 반드시 "핀 고정된(pinned) 스냅샷 ID"로 등록한다 (예: gpt-4o-2024-08-06).
"gpt-4o" 같은 별칭(alias)으로 등록하면 안 된다 — OpenAI Responses API는 별칭으로 요청해도
응답의 `model` 필드에는 별칭이 resolve된 실제 스냅샷 ID를 돌려준다
(adapters/openai.py의 `_parse_response`가 `data.get("model")`을 신뢰하기 때문).
카탈로그 키가 별칭이면 이 응답 문자열과 절대 일치하지 않아 CostCalculator가 매번
"Unknown model"로 실패하고 비용이 조용히 0으로 기록된다. 요청도 이 카탈로그 ID를 그대로
보내므로(OpenAI는 스냅샷 ID로 직접 호출 가능), 별칭 없이 항상 정확히 일치한다.
반대로 Gemini 어댑터는 응답을 신뢰하지 않고 요청에 쓴 카탈로그 ID를 그대로 돌려주므로
(adapters/gemini.py `_parse_response`) 이 문제가 없다 — 기존 방식 유지.

주의: OpenAI가 별칭이 가리키는 기본 스냅샷을 교체하면(공지 후 구버전 스냅샷은 통상 3개월
후 폐기) 여기 등록된 스냅샷 ID도 갱신해야 한다. 폐기 전 새 스냅샷 ID로 옮기지 않으면 해당
모델 호출 자체가 실패한다.

호출 코드에서 실수로 별칭(카탈로그에 아예 없는 ID)을 CompletionRequest.model에 넣으면,
adapters/openai.py::OpenAIAdapter._call_provider의 SUPPORTED_OPENAI_MODELS 체크가 실제
HTTP 요청 전에 걸러내 즉시 LLMAPIError를 던진다 — 이 경우는 BaseLLMAdapter._safe_cost의
"0원+워닝" 경로를 타지 않고 호출 자체가 실패로 전파된다(silent하게 0원 과금되는 게 아님).
OpenAI 모델은 항상 위 pinned 스냅샷 ID로만 지정할 것.
"""

from dataclasses import dataclass
from decimal import Decimal

Provider = str  # "anthropic" | "gemini" | "openai"


@dataclass
class ModelPricing:
    """1M 토큰당 USD 단가."""

    input: Decimal
    output: Decimal
    cached_input: Decimal


@dataclass
class ModelSpec:
    id: str
    provider: Provider
    pricing: ModelPricing | None = None  # None이면 단가 미책정 (비용 0)
    default: bool = False  # provider 내 기본 모델 (미지정 시 선택)


MODELS: tuple[ModelSpec, ...] = (
    # Anthropic (Claude)
    ModelSpec(
        "claude-opus-4-7",
        "anthropic",
        ModelPricing(Decimal("5"), Decimal("25"), Decimal("0.50")),
    ),
    ModelSpec(
        "claude-sonnet-4-6",
        "anthropic",
        ModelPricing(Decimal("3"), Decimal("15"), Decimal("0.30")),
    ),
    ModelSpec(
        "claude-haiku-4-5",
        "anthropic",
        ModelPricing(Decimal("1"), Decimal("5"), Decimal("0.10")),
    ),
    # Google (Gemini) — text/image/video 기준 단가(오디오 단가는 미표현)
    # cached_input=context caching. 출처: ai.google.dev/gemini-api/docs/pricing (2026-07-28 확인)
    # 2.5 계열은 신규 사용자에게 종료됨(2026-08-03 실측: generateContent 404 "no longer
    # available to new users") — 과거 비용 행 단가 해석을 위해 유지만 하고, 기본은 3.5-flash-lite.
    ModelSpec(
        "gemini-2.5-flash-lite",
        "gemini",
        ModelPricing(Decimal("0.10"), Decimal("0.40"), Decimal("0.01")),
    ),
    ModelSpec(
        "gemini-2.5-flash",
        "gemini",
        ModelPricing(Decimal("0.30"), Decimal("2.50"), Decimal("0.03")),
    ),
    ModelSpec(
        "gemini-3.1-flash-lite",
        "gemini",
        ModelPricing(Decimal("0.25"), Decimal("1.50"), Decimal("0.025")),
    ),
    ModelSpec(
        "gemini-3.5-flash-lite",
        "gemini",
        ModelPricing(Decimal("0.30"), Decimal("2.50"), Decimal("0.03")),
        default=True,  # 2.5 종료 승계 — Gemini provider 기본
    ),
    ModelSpec(
        "gemini-3.5-flash",
        "gemini",
        ModelPricing(Decimal("1.50"), Decimal("9.00"), Decimal("0.15")),
    ),
    ModelSpec(
        "gemini-3.6-flash",
        "gemini",
        ModelPricing(Decimal("1.50"), Decimal("7.50"), Decimal("0.15")),
    ),
    # gemini-2.5-pro / gemini-3.1-pro-preview는 프롬프트 길이(<=200k/>200k)에 따라 단가가
    # 갈리는 구간별(tiered) 요금이라 ModelPricing(단일 input/output)으로 정확히 표현할 수
    # 없어 이번엔 제외한다. gemini-embedding-001은 output/cached_input 토큰 개념이 없고
    # 이 코드베이스에 임베딩 호출 경로 자체가 없어 제외. 이미지/오디오/라이브 특수 모델은
    # 토큰이 아닌 이미지·초 단위 과금이라 마찬가지로 제외.
    #
    # OpenAI (GPT) — Standard tier 단가. 모두 별칭이 아닌 핀 고정 스냅샷 ID로 등록
    # (이유는 모듈 docstring 참고). 출처: developers.openai.com/api/docs/pricing,
    # developers.openai.com/api/docs/models/<id> (2026-07-28 확인)
    ModelSpec(
        "gpt-5.4-nano-2026-03-17",
        "openai",
        ModelPricing(Decimal("0.20"), Decimal("1.25"), Decimal("0.02")),
        default=True,
    ),
    ModelSpec(
        "gpt-5.4-mini-2026-03-17",
        "openai",
        ModelPricing(Decimal("0.75"), Decimal("4.50"), Decimal("0.075")),
    ),
    ModelSpec(
        "gpt-5.4-2026-03-05",
        "openai",
        ModelPricing(Decimal("2.50"), Decimal("15.00"), Decimal("0.25")),
    ),
    ModelSpec(
        "gpt-5.4-pro-2026-03-05",
        "openai",
        # pro tier는 캐싱을 제공하지 않음(공식 단가표 cached_input="—") -> 0으로 표기
        ModelPricing(Decimal("30.00"), Decimal("180.00"), Decimal("0")),
    ),
    ModelSpec(
        "gpt-5-2025-08-07",
        "openai",
        ModelPricing(Decimal("1.25"), Decimal("10.00"), Decimal("0.125")),
    ),
    ModelSpec(
        "gpt-5-mini-2025-08-07",
        "openai",
        ModelPricing(Decimal("0.25"), Decimal("2.00"), Decimal("0.025")),
    ),
    ModelSpec(
        "gpt-5-nano-2025-08-07",
        "openai",
        ModelPricing(Decimal("0.05"), Decimal("0.40"), Decimal("0.005")),
    ),
    ModelSpec(
        "gpt-4.1-2025-04-14",
        "openai",
        ModelPricing(Decimal("2.00"), Decimal("8.00"), Decimal("0.50")),
    ),
    ModelSpec(
        "gpt-4.1-mini-2025-04-14",
        "openai",
        ModelPricing(Decimal("0.40"), Decimal("1.60"), Decimal("0.10")),
    ),
    ModelSpec(
        "gpt-4.1-nano-2025-04-14",
        "openai",
        ModelPricing(Decimal("0.10"), Decimal("0.40"), Decimal("0.025")),
    ),
    ModelSpec(
        "gpt-4o-2024-08-06",
        "openai",
        ModelPricing(Decimal("2.50"), Decimal("10.00"), Decimal("1.25")),
    ),
    ModelSpec(
        "gpt-4o-mini-2024-07-18",
        "openai",
        ModelPricing(Decimal("0.15"), Decimal("0.60"), Decimal("0.075")),
    ),
    ModelSpec(
        "o3-2025-04-16",
        "openai",
        ModelPricing(Decimal("2.00"), Decimal("8.00"), Decimal("0.50")),
    ),
    ModelSpec(
        "o4-mini-2025-04-16",
        "openai",
        ModelPricing(Decimal("1.10"), Decimal("4.40"), Decimal("0.275")),
    ),
    # gpt-5.5/gpt-5.5-pro/gpt-5.2/gpt-5.1/gpt-5.6-* 등 상위·구버전 티어는 실사용 계획이
    # 정해지지 않아 이번엔 범위 밖으로 제외.
)

BY_ID: dict[str, ModelSpec] = {m.id: m for m in MODELS}


def by_provider(provider: Provider) -> tuple[ModelSpec, ...]:
    """provider에 속한 모델 스펙들."""
    return tuple(m for m in MODELS if m.provider == provider)


def supported_ids(provider: Provider) -> frozenset[str]:
    """provider가 지원하는 모델 ID 집합."""
    return frozenset(m.id for m in by_provider(provider))


def default_id(provider: Provider) -> str:
    """provider의 기본 모델 ID."""
    for m in by_provider(provider):
        if m.default:
            return m.id
    raise KeyError(f"기본 모델이 지정되지 않은 provider입니다: {provider}")
