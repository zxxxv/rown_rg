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


# (구 tests/unit/test_llm_cost.py에서 병합 - 같은 대상(CostCalculator)의
#  테스트 파일이 두 곳이라 한쪽만 고치는 사고를 막는다, 2026-09-04 정리)
class TestCostCalculatorNewModels:
    """gemini/openai 신규 등록 모델의 단가 계산 검증."""

    @pytest.mark.parametrize(
        ("model", "input_tokens", "output_tokens", "cached_input_tokens", "expected"),
        [
            # Anthropic — Fable 5는 Opus 5의 정확히 2배 단가($10/$50)
            ("claude-fable-5", 1_000_000, 1_000_000, 0, Decimal("60.00")),
            ("claude-opus-5", 1_000_000, 1_000_000, 0, Decimal("30.00")),
            # 캐시 읽기는 입력의 10%로 등록했다(카탈로그 관행)
            ("claude-fable-5", 0, 0, 1_000_000, Decimal("1.00")),
            # Gemini
            ("gemini-3.1-flash-lite", 1_000_000, 1_000_000, 0, Decimal("1.75")),
            ("gemini-3.5-flash-lite", 1_000_000, 1_000_000, 0, Decimal("2.80")),
            ("gemini-3.5-flash", 1_000_000, 1_000_000, 0, Decimal("10.50")),
            ("gemini-3.6-flash", 1_000_000, 1_000_000, 0, Decimal("9.00")),
            # OpenAI (모두 핀 고정 스냅샷 ID)
            ("gpt-5.4-nano-2026-03-17", 1_000_000, 1_000_000, 0, Decimal("1.45")),
            ("gpt-5.4-mini-2026-03-17", 1_000_000, 1_000_000, 0, Decimal("5.25")),
            ("gpt-5.4-2026-03-05", 1_000_000, 1_000_000, 0, Decimal("17.50")),
            ("gpt-5.4-pro-2026-03-05", 1_000_000, 1_000_000, 0, Decimal("210.00")),
            ("gpt-5-2025-08-07", 1_000_000, 1_000_000, 0, Decimal("11.25")),
            ("gpt-5-mini-2025-08-07", 1_000_000, 1_000_000, 0, Decimal("2.25")),
            ("gpt-5-nano-2025-08-07", 1_000_000, 1_000_000, 0, Decimal("0.45")),
            ("gpt-4.1-2025-04-14", 1_000_000, 1_000_000, 0, Decimal("10.00")),
            ("gpt-4.1-mini-2025-04-14", 1_000_000, 1_000_000, 0, Decimal("2.00")),
            ("gpt-4.1-nano-2025-04-14", 1_000_000, 1_000_000, 0, Decimal("0.50")),
            ("gpt-4o-2024-08-06", 1_000_000, 1_000_000, 0, Decimal("12.50")),
            ("gpt-4o-mini-2024-07-18", 1_000_000, 1_000_000, 0, Decimal("0.75")),
            ("o3-2025-04-16", 1_000_000, 1_000_000, 0, Decimal("10.00")),
            ("o4-mini-2025-04-16", 1_000_000, 1_000_000, 0, Decimal("5.50")),
        ],
    )
    def test_calculate_matches_official_pricing(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        expected: Decimal,
    ) -> None:
        cost = CostCalculator.calculate(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        assert cost == expected

    def test_gpt_5_4_pro_has_no_caching_discount(self) -> None:
        # pro tier는 공식 단가표에 cached_input이 없어("—") 0으로 등록했다 —
        # cached 토큰을 보내도 그만큼은 비용에 전혀 반영되지 않아야 한다.
        with_cache = CostCalculator.calculate(
            model="gpt-5.4-pro-2026-03-05",
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=1_000_000,
        )
        assert with_cache == Decimal("0")

    @pytest.mark.parametrize(
        "alias",
        [
            "gpt-5.4-nano",  # 구 별칭 ID — 이번 변경으로 더 이상 카탈로그 키가 아님
            "gpt-5.4-mini",
            "gpt-4o",  # 별칭으로는 절대 등록하지 않음(모듈 docstring 참고)
            "gpt-4.1",
        ],
    )
    def test_bare_alias_is_unknown(self, alias: str) -> None:
        with pytest.raises(ValueError, match="Unknown model"):
            CostCalculator.calculate(
                model=alias, input_tokens=100, output_tokens=100, cached_input_tokens=0
            )
