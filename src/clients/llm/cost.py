from decimal import Decimal

from src.clients.llm.models import MODELS

# 모델별 1M 토큰당 USD 단가. 카탈로그(models.py)에서 파생 — 단가 미책정 모델은 제외된다.
PRICING: dict[str, dict[str, Decimal]] = {
    m.id: {
        "input": m.pricing.input,
        "output": m.pricing.output,
        "cached_input": m.pricing.cached_input,
    }
    for m in MODELS
    if m.pricing is not None
}

PER_MILLION = Decimal("1000000")
COST_QUANTUM = Decimal("0.000001")

# 캐시 쓰기 프리미엄 — Anthropic 5분 TTL 기준 입력 단가의 1.25배.
# 캐시 쓰기 토큰을 보고하는 provider는 현재 Anthropic뿐(타 어댑터는 0으로 옴).
CACHE_WRITE_MULTIPLIER = Decimal("1.25")


def _resolve_pricing(model: str) -> dict[str, Decimal] | None:
    """단가 조회 — 정확 일치 우선, 없으면 접두사 매칭.

    provider API는 응답에 날짜 붙은 전체 ID를 돌려줄 수 있다(예: 요청은
    claude-haiku-4-5, 응답 model은 claude-haiku-4-5-20251001). 접두사 매칭이
    없으면 이런 호출이 전부 비용 0으로 기록된다(2026-07-22 스모크에서 실측).
    """
    if model in PRICING:
        return PRICING[model]
    candidates = [catalog_id for catalog_id in PRICING if model.startswith(catalog_id)]
    if not candidates:
        return None
    return PRICING[max(candidates, key=len)]


class CostCalculator:
    @staticmethod
    def calculate(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_input_tokens: int = 0,
    ) -> Decimal:
        prices = _resolve_pricing(model)
        if prices is None:
            raise ValueError(f"Unknown model: {model}")
        cost = (
            (Decimal(input_tokens) * prices["input"])
            + (Decimal(cached_input_tokens) * prices["cached_input"])
            + (Decimal(cache_write_input_tokens) * prices["input"] * CACHE_WRITE_MULTIPLIER)
            + (Decimal(output_tokens) * prices["output"])
        ) / PER_MILLION
        return cost.quantize(COST_QUANTUM)
