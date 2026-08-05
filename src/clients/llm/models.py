"""provider별 모델 카탈로그

여기 한 곳만 고치면 라우팅(router)·비용(cost)·지원목록(adapters)이 모두 따라온다.
- 새 모델 추가: MODELS에 ModelSpec 한 줄
- 단가 미책정(pricing=None) 모델은 비용 0으로 처리된다(BaseLLMAdapter._safe_cost)
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
    # Google (Gemini) — text 기준 단가, cached_input=context caching
    # 출처: ai.google.dev/gemini-api/docs/pricing
    # 2.5 계열은 신규 사용자에게 종료됨(2026-08-03 실측: generateContent 404
    # "no longer available to new users") — 과거 비용 행 단가 해석을 위해 유지만 한다.
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
        # 실작동 최저가(2026-08-04 실측: generateContent 200). cached는 입력의 10% 관례.
        ModelPricing(Decimal("0.25"), Decimal("1.50"), Decimal("0.025")),
        default=True,
    ),
    ModelSpec(
        "gemini-3.5-flash-lite",
        "gemini",
        ModelPricing(Decimal("0.30"), Decimal("2.50"), Decimal("0.03")),
    ),
    ModelSpec(
        "gemini-3.5-flash",
        "gemini",
        ModelPricing(Decimal("1.50"), Decimal("9.00"), Decimal("0.15")),
    ),
    # OpenAI (GPT) — Standard tier 단가, cached_input=cached input
    # 출처: developers.openai.com/api/docs/pricing
    ModelSpec(
        "gpt-5.4-nano",
        "openai",
        ModelPricing(Decimal("0.20"), Decimal("1.25"), Decimal("0.02")),
        default=True,
    ),
    ModelSpec(
        "gpt-5.4-mini",
        "openai",
        ModelPricing(Decimal("0.75"), Decimal("4.50"), Decimal("0.075")),
    ),
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
