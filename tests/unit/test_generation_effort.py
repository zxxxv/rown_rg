"""작성 콜 추론 예산 정책(write_effort) — 어느 모델에 effort를 낮추는가.

정책: thinking이 기본 켜지는 모델(Opus 5·Sonnet 5)만 낮춘다. Sonnet 4.6은
thinking이 원래 꺼진 채 돌아 절감이 없는데 생성 동작만 바뀔 수 있고(기본 high
→ low), 타 provider(GPT)는 이 파라미터 개념 자체가 없다.
"""

from src.services.generation.effort import write_effort


class TestWriteEffort:
    def test_thinking_default_on_model_gets_configured_effort(self) -> None:
        # 설정 기본값(config.write_effort="low")이 그대로 전달된다
        assert write_effort("claude-opus-5") == "low"

    def test_thinking_off_models_are_untouched(self) -> None:
        # Sonnet 4.6(표준 작성)·Haiku·GPT는 None — 파라미터를 아예 보내지 않는다
        assert write_effort("claude-sonnet-4-6") is None
        assert write_effort("claude-haiku-4-5") is None
        assert write_effort("gpt-5.4-mini-2026-03-17") is None

    def test_unknown_model_is_conservative(self) -> None:
        assert write_effort("some-future-model") is None
