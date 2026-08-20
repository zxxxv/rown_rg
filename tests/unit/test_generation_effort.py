"""작성 콜 추론 예산 정책(write_effort) — 어느 모델에 effort를 낮추는가.

정책: thinking이 기본 켜지는 모델(Opus 5·Sonnet 5)만 낮춘다. Sonnet 4.6은
thinking이 원래 꺼진 채 돌아 절감이 없는데 생성 동작만 바뀔 수 있고(기본 high
→ low), 타 provider(GPT)는 이 파라미터 개념 자체가 없다.
"""

from src.core import app_settings
from src.services.generation.effort import write_effort


class TestWriteEffort:
    def test_thinking_default_on_model_gets_configured_effort(self) -> None:
        # 설정 기본값(config.write_effort="low")이 그대로 전달된다
        assert write_effort("claude-opus-5") == "low"

    def test_synthesis_uses_separate_setting(self) -> None:
        # 종합 절(builds_on 보유)은 별도 키 — 기본 medium
        assert write_effort("claude-opus-5", synthesis=True) == "medium"

    def test_thinking_off_models_are_untouched(self) -> None:
        # Sonnet 4.6(표준 작성)·Haiku·GPT는 None — 파라미터를 아예 보내지 않는다
        assert write_effort("claude-sonnet-4-6") is None
        assert write_effort("claude-haiku-4-5") is None
        assert write_effort("gpt-5.4-mini-2026-03-17") is None

    def test_unknown_model_is_conservative(self) -> None:
        assert write_effort("some-future-model") is None


class TestFableUsesOwnKeys:
    """Fable은 fable_ 접두 키를 읽는다 — Opus 키를 조여도 딸려오지 않아야 한다."""

    def test_defaults_match_config(self) -> None:
        assert write_effort("claude-fable-5") == "low"
        assert write_effort("claude-fable-5", synthesis=True) == "medium"

    def test_fable_key_is_independent_of_opus_key(self) -> None:
        # DB 오버라이드로 Fable만 올려도 Opus는 그대로여야 한다(그 역도 같다).
        app_settings._cache["fable_write_effort"] = "high"
        try:
            assert write_effort("claude-fable-5") == "high"
            assert write_effort("claude-opus-5") == "low"
        finally:
            app_settings._cache.pop("fable_write_effort", None)
