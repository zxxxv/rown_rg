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


class CostCalculator:
    @staticmethod
    def calculate(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> Decimal:
        if model not in PRICING:
            raise ValueError(f"Unknown model: {model}")
        prices = PRICING[model]
        cost = (
            (Decimal(input_tokens) * prices["input"])
            + (Decimal(cached_input_tokens) * prices["cached_input"])
            + (Decimal(output_tokens) * prices["output"])
        ) / PER_MILLION
        return cost.quantize(COST_QUANTUM)
