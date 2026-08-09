"""파트당 목표 분량 — 계획이 요청보다 적게 나눠도 절 목표를 지킨다.

실측(2026-08-10): 목표 20,000자 절이 6파트로 계획되자 6 x 2,250(고정 파트 목표)
= 13,500자에서 정확히 멈췄다. 프롬프트에 절 목표를 실어도 파트 곱이 천장이었다.
"""

from __future__ import annotations

from src.core.config import settings
from src.services.generation.split_writer import _MAX_PART_GOAL_CHARS, _part_tail


def _goal_text(n_parts: int, per_part_goal: int) -> str:
    return _part_tail(
        1, n_parts, "소주제", ["소주제", "다른 소주제"], [1, 2], [], [], per_part_goal=per_part_goal
    )


class TestPerPartGoal:
    def test_goal_appears_in_instruction(self):
        assert "3,000자" in _goal_text(6, 3000)

    def test_default_is_settings_value(self):
        assert f"{settings.write_split_chars_per_part:,}자" in _goal_text(6, 0)

    def test_cap_exists_for_single_call_reality(self):
        # 한 번의 호출로 낼 수 있는 분량에는 현실 상한이 있다(실측 4~8천자).
        assert 3000 <= _MAX_PART_GOAL_CHARS <= 8000
