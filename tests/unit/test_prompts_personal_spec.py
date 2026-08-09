"""개인 프롬프트 spec — 목표 분량 3단과 규칙 슬롯 계약.

개인 에이전트에는 volume_target이 없어 배정할수록 절이 짧아졌다(2026-08-10).
spec.volume이 그 값을 채우고, 지정이 없으면 '보통'을 기본으로 준다.
"""

from __future__ import annotations

from src.prompts import list_components
from src.services.prompts.personal import RULE_SLOTS, VOLUME_PRESETS, volume_from_spec


class TestVolumeFromSpec:
    def test_known_levels(self):
        for level in ("short", "normal", "long"):
            target = volume_from_spec({"volume": level})
            assert target is not None
            assert target.min_chars < target.max_chars

    def test_levels_are_ordered(self):
        short, normal, long_ = (VOLUME_PRESETS[k] for k in ("short", "normal", "long"))
        assert short.min_chars < normal.min_chars < long_.min_chars

    def test_missing_or_unknown_is_none(self):
        assert volume_from_spec(None) is None
        assert volume_from_spec({}) is None
        assert volume_from_spec({"volume": "아주길게"}) is None
        assert volume_from_spec("문자열") is None


class TestRuleSlots:
    def test_slots_exist_in_system_catalog(self):
        """슬롯 이름은 실제 시스템 조각과 1:1이어야 한다 — 어긋나면 교체가 조용히 실패한다."""
        available = set(list_components())
        assert set(RULE_SLOTS) <= available

    def test_slot_order_is_fixed(self):
        assert RULE_SLOTS == ("agent_source_rules", "agent_visual_rules", "agent_writing_style")
