"""한도 enforcement 검증 — 순수 판정 + enforce 토글 + 관문(BaseLLMAdapter) 배선 (실DB 없음)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.clients.llm import quota_gate, token_tracker
from src.clients.llm.adapters.base import BaseLLMAdapter, RetryKind
from src.clients.llm.base import CompletionRequest, CompletionResponse, Message
from src.clients.llm.cassette import CassetteManager, compute_input_hash, resolve_cache_key
from src.core.config import settings
from src.core.exceptions import QuotaExceededError


class TestCheckLimits:
    def test_under_both_limits_passes(self):
        quota_gate.check_limits(
            org_cost=Decimal("100"),
            org_limit=Decimal("3000"),
            user_cost=Decimal("10"),
            user_limit=Decimal("200"),
        )

    def test_org_limit_reached_blocks(self):
        with pytest.raises(QuotaExceededError) as exc:
            quota_gate.check_limits(org_cost=Decimal("3000"), org_limit=Decimal("3000"))
        assert exc.value.code == "ORG_QUOTA_EXCEEDED"

    def test_user_limit_reached_blocks(self):
        with pytest.raises(QuotaExceededError) as exc:
            quota_gate.check_limits(
                org_cost=Decimal("100"),
                org_limit=Decimal("3000"),
                user_cost=Decimal("200"),
                user_limit=Decimal("200"),
            )
        assert exc.value.code == "USER_QUOTA_EXCEEDED"

    def test_org_check_takes_precedence(self):
        with pytest.raises(QuotaExceededError) as exc:
            quota_gate.check_limits(
                org_cost=Decimal("9999"),
                org_limit=Decimal("3000"),
                user_cost=Decimal("999"),
                user_limit=Decimal("200"),
            )
        assert exc.value.code == "ORG_QUOTA_EXCEEDED"

    def test_missing_user_info_skips_user_check(self):
        quota_gate.check_limits(
            org_cost=Decimal("100"), org_limit=Decimal("3000"), user_cost=None, user_limit=None
        )


class TestEnforce:
    async def test_disabled_toggle_skips_fetch(self, monkeypatch: pytest.MonkeyPatch):
        async def _boom(user_id: object) -> object:
            raise AssertionError("enforcement off이면 조회 자체가 없어야 함")

        monkeypatch.setattr(settings, "quota_enforcement_enabled", False)
        monkeypatch.setattr(quota_gate, "_fetch_usage", _boom)
        await quota_gate.enforce()

    async def test_enabled_blocks_on_exceeded(self, monkeypatch: pytest.MonkeyPatch):
        async def _fetch(user_id: object) -> tuple[Decimal, Decimal | None, Decimal | None]:
            return Decimal("50"), Decimal("200"), Decimal("200")

        monkeypatch.setattr(settings, "quota_enforcement_enabled", True)
        monkeypatch.setattr(quota_gate, "_fetch_usage", _fetch)
        with pytest.raises(QuotaExceededError):
            await quota_gate.enforce()

    async def test_enabled_passes_under_limit(self, monkeypatch: pytest.MonkeyPatch):
        async def _fetch(user_id: object) -> tuple[Decimal, Decimal | None, Decimal | None]:
            return Decimal("50"), Decimal("10"), Decimal("200")

        monkeypatch.setattr(settings, "quota_enforcement_enabled", True)
        monkeypatch.setattr(quota_gate, "_fetch_usage", _fetch)
        await quota_gate.enforce()


class _FakeAdapter(BaseLLMAdapter):
    """provider 호출을 세기만 하는 최소 어댑터 — 관문 배선 검증용."""

    provider = "fake"

    def __init__(self, **kwargs: Any) -> None:
        self.provider_calls = 0
        super().__init__(**kwargs)

    def _create_client(self, api_key: str) -> Any:
        return object()

    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        self.provider_calls += 1
        return CompletionResponse(
            content="응답",
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )

    def _classify_error(self, exc: Exception) -> RetryKind | None:
        return None


def _request() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="질문")],
        model="claude-haiku-4-5",
        cache_key=None,
    )


@pytest.fixture(autouse=True)
def no_usage_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    """fire-and-forget 토큰 기록이 테스트에서 DB를 찌르지 않게 무력화."""

    async def _noop(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(token_tracker, "record_usage_safe", _noop)


class TestAdapterGateWiring:
    async def test_live_call_enforces_quota(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        calls: list[str] = []

        async def _enforce() -> None:
            calls.append("enforced")

        monkeypatch.setattr(quota_gate, "enforce", _enforce)
        adapter = _FakeAdapter(api_key="k", mode="live", cassette_manager=CassetteManager(tmp_path))
        await adapter.complete(_request())
        assert calls == ["enforced"]
        assert adapter.provider_calls == 1

    async def test_blocked_call_never_reaches_provider(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        async def _enforce() -> None:
            raise QuotaExceededError("한도 초과", code="USER_QUOTA_EXCEEDED")

        monkeypatch.setattr(quota_gate, "enforce", _enforce)
        adapter = _FakeAdapter(api_key="k", mode="live", cassette_manager=CassetteManager(tmp_path))
        with pytest.raises(QuotaExceededError):
            await adapter.complete(_request())
        assert adapter.provider_calls == 0

    async def test_replay_skips_enforcement(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        async def _enforce() -> None:
            raise AssertionError("replay는 한도 검사 없이 통과해야 함")

        monkeypatch.setattr(quota_gate, "enforce", _enforce)
        manager = CassetteManager(tmp_path)
        request = _request()
        input_hash = compute_input_hash(request)
        cache_key = resolve_cache_key(request, input_hash)
        recorded = CompletionResponse(
            content="녹화된 응답",
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )
        await manager.save("unknown", cache_key, input_hash, request, recorded)

        adapter = _FakeAdapter(api_key="k", mode="replay", cassette_manager=manager)
        response = await adapter.complete(request)
        assert response.content == "녹화된 응답"
        assert adapter.provider_calls == 0


class TestUserQuotaFallback:
    async def test_enforce_uses_context_user(self, monkeypatch: pytest.MonkeyPatch):
        """token_context의 user_id가 조회로 전달되는지."""
        seen: list[object] = []
        user_id = uuid4()

        async def _fetch(uid: object) -> tuple[Decimal, Decimal | None, Decimal | None]:
            seen.append(uid)
            return Decimal("0"), Decimal("0"), Decimal("100")

        monkeypatch.setattr(settings, "quota_enforcement_enabled", True)
        monkeypatch.setattr(quota_gate, "_fetch_usage", _fetch)
        with token_tracker.token_context(user_id=user_id, operation="test"):
            await quota_gate.enforce()
        assert seen == [user_id]
