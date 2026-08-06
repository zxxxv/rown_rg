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

    def test_cache_write_priced_at_125_percent(self):
        # 캐시 쓰기는 입력 단가의 1.25배(Anthropic 5분 TTL) — 이 분리가 없으면
        # 프롬프트 캐싱을 켠 순간 쓰기 토큰이 비용 계정에서 통째로 사라진다.
        cost = CostCalculator.calculate(
            "claude-sonnet-4-6", 0, 0, cache_write_input_tokens=1_000_000
        )
        assert cost == Decimal("3.75")  # 1M × $3 × 1.25

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            CostCalculator.calculate("gpt-999-unknown", 100, 100)
