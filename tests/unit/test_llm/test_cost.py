"""비용 계산 검증 — 단가 조회의 접두사 매칭 (버전 붙은 응답 모델 ID 대응)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.clients.llm.cost import CostCalculator


class TestCostCalculator:
    def test_exact_alias(self):
        cost = CostCalculator.calculate("claude-haiku-4-5", 1_000_000, 0)
        assert cost == Decimal("1")

    def test_versioned_response_model_resolves_by_prefix(self):
        """API가 날짜 붙은 전체 ID를 돌려줘도 별칭 단가로 계산돼야 한다."""
        cost = CostCalculator.calculate("claude-haiku-4-5-20251001", 1_000_000, 200_000)
        assert cost == Decimal("2")  # 1M in × $1 + 0.2M out × $5

    def test_cached_input_priced_separately(self):
        cost = CostCalculator.calculate("claude-haiku-4-5", 0, 0, cached_input_tokens=1_000_000)
        assert cost == Decimal("0.1")

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            CostCalculator.calculate("gpt-999-unknown", 100, 100)
