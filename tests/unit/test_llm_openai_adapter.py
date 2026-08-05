from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from structlog.testing import capture_logs

from src.clients.llm.adapters.openai import SUPPORTED_OPENAI_MODELS, OpenAIAdapter
from src.clients.llm.base import CompletionResponse
from src.clients.llm.cassette import CassetteManager


def _make_adapter(tmp_path: Path) -> OpenAIAdapter:
    # mode="replay" -> _client는 None, api_key 없이도 생성 가능 (네트워크 호출 없음).
    return OpenAIAdapter(
        api_key="",
        mode="replay",
        cassette_manager=CassetteManager(tmp_path / "cassettes"),
    )


class TestSupportedOpenAIModels:
    def test_pinned_snapshots_are_registered(self) -> None:
        assert "gpt-4o-2024-08-06" in SUPPORTED_OPENAI_MODELS
        assert "gpt-5.4-nano-2026-03-17" in SUPPORTED_OPENAI_MODELS

    def test_bare_aliases_are_not_registered(self) -> None:
        # 별칭으로는 등록하지 않는다 — 응답의 model 필드가 별칭이 아니라 스냅샷으로
        # 돌아오기 때문에(아래 TestParseResponseTrustsApiModel), 별칭을 키로 쓰면
        # CostCalculator가 항상 실패한다.
        assert "gpt-4o" not in SUPPORTED_OPENAI_MODELS
        assert "gpt-4.1" not in SUPPORTED_OPENAI_MODELS
        assert "gpt-5.4-nano" not in SUPPORTED_OPENAI_MODELS


class TestParseResponseTrustsApiModel:
    """OpenAIAdapter._parse_response는 요청 모델이 아니라 API 응답의 model 필드를 신뢰한다."""

    def test_response_model_overrides_requested_alias(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        data = {
            "model": "gpt-4o-2024-08-06",  # 별칭 "gpt-4o"로 요청해도 API가 돌려주는 실제 스냅샷
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "hi"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "input_tokens_details": {}},
        }

        response = adapter._parse_response(data, fallback_model="gpt-4o")

        assert response.model == "gpt-4o-2024-08-06"
        assert response.model != "gpt-4o"

    def test_falls_back_to_requested_model_when_api_omits_it(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        data = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "hi"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "input_tokens_details": {}},
        }

        response = adapter._parse_response(data, fallback_model="gpt-4o-2024-08-06")

        assert response.model == "gpt-4o-2024-08-06"


class TestSafeCostWithPinnedSnapshots:
    """등록된 스냅샷 ID로는 비용이 정상 계산되고, 별칭이 새어 들어오면 0원+워닝으로 격리된다."""

    def test_registered_snapshot_is_priced(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        response = CompletionResponse(
            content="hi",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="gpt-4o-2024-08-06",
            stop_reason="completed",
        )

        cost = adapter._safe_cost(response)

        assert cost == Decimal("12.50")

    def test_unregistered_alias_falls_back_to_zero_with_warning(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        # 만약 어떤 이유로든(예: 미래에 다른 provider가 별칭을 그대로 돌려주는 경우) 카탈로그에
        # 없는 문자열이 response.model로 들어오면, 비용은 조용히 0이 되지만 워닝 로그는 남는다.
        response = CompletionResponse(
            content="hi",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="gpt-4o",  # 별칭 — 카탈로그에 없음
            stop_reason="completed",
        )

        with capture_logs() as logs:
            cost = adapter._safe_cost(response)

        assert cost == Decimal("0")
        assert any(
            log["event"] == "llm.cost.unpriced_model" and log["model"] == "gpt-4o" for log in logs
        )
