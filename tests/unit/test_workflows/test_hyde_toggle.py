"""HyDE 프로젝트별 on/off 토글 — config.hyde_enabled가 전역 기본값을 오버라이드.

_hyde_enabled_for는 순수 판정 함수라 실검색·실LLM 없이 검증한다. 배선(팩토리가
이 값으로 expander를 만들거나 만들지 않음)은 _semantic 계약(query_expander=None이면
원 쿼리 통과)이 이미 test_hyde/test_retrieval에서 커버한다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.config import settings
from src.core.state import ProjectState
from src.workflows.stages import _hyde_enabled_for


def _state(options: dict) -> ProjectState:
    return ProjectState(user_id=uuid4(), topic="인구 고령화 대응", options=options)


class TestHydeToggle:
    def test_project_on_overrides_global_off(self, monkeypatch):
        monkeypatch.setattr(settings, "hyde_enabled", False)
        assert _hyde_enabled_for(_state({"hyde_enabled": True})) is True

    def test_project_off_overrides_global_on(self, monkeypatch):
        monkeypatch.setattr(settings, "hyde_enabled", True)
        assert _hyde_enabled_for(_state({"hyde_enabled": False})) is False

    def test_falls_back_to_global_default_when_unset(self, monkeypatch):
        monkeypatch.setattr(settings, "hyde_enabled", True)
        assert _hyde_enabled_for(_state({})) is True

        monkeypatch.setattr(settings, "hyde_enabled", False)
        assert _hyde_enabled_for(_state({})) is False

    def test_ignores_unrelated_config_keys(self, monkeypatch):
        monkeypatch.setattr(settings, "hyde_enabled", False)
        assert _hyde_enabled_for(_state({"outline": {"chapters": []}})) is False

    @pytest.mark.parametrize("bad_options", [None, "on", 1])
    def test_non_dict_options_fall_back_to_global(self, monkeypatch, bad_options):
        # options는 dict 타입이지만 레거시/비정상 데이터 방어 — 전역값으로 폴백한다.
        # model_construct로 검증을 우회해 비정상 값을 주입한다.
        monkeypatch.setattr(settings, "hyde_enabled", True)
        state = ProjectState.model_construct(user_id=uuid4(), topic="주제", options=bad_options)
        assert _hyde_enabled_for(state) is True
