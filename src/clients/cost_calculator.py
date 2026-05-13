from decimal import Decimal

# 모델별 1M 토큰당 USD 단가 (2026-05 기준)
PRICING: dict[str, dict[str, Decimal]] = {
    "claude-sonnet-4-6": {
        "input": Decimal("3"),
        "output": Decimal("15"),
        "cached_input": Decimal("0.30"),
    },
    "claude-haiku-4-5": {
        "input": Decimal("1"),
        "output": Decimal("5"),
        "cached_input": Decimal("0.10"),
    },
    "claude-opus-4-7": {
        "input": Decimal("5"),
        "output": Decimal("25"),
        "cached_input": Decimal("0.50"),
    },
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
