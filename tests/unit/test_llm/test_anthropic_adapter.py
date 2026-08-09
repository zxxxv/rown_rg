"""Anthropic 어댑터의 프롬프트 캐싱 배선 검증.

실 API 없이 가짜 클라이언트로 요청 kwargs와 usage 집계를 확인한다:
- 단발 경로: system 블록 리스트 + cache_control, cache_creation 토큰 포착
- 웹검색(pause_turn) 경로: top-level cache_control + 요청 메시지 브레이크포인트,
  턴 누적 캐시쓰기 집계
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.clients.llm.adapters.anthropic import AnthropicAdapter
from src.clients.llm.base import CompletionRequest, Message, WebSearchConfig
from src.clients.llm.cassette import CassetteManager


def _make_adapter(tmp_path: Path) -> AnthropicAdapter:
    # mode="replay" -> 실 클라이언트 미생성. 테스트가 가짜를 주입한다.
    return AnthropicAdapter(
        api_key="",
        mode="replay",
        cassette_manager=CassetteManager(tmp_path / "cassettes"),
    )


def _usage(**kw: int) -> SimpleNamespace:
    base = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


class _FakeStream:
    """messages.stream()의 async 컨텍스트 매니저 계약만 흉내낸다."""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self._result


class _FakeMessages:
    def __init__(self, results: list[Any]) -> None:
        self._results = results
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._results[len(self.calls) - 1]

    def stream(self, **kwargs: Any) -> _FakeStream:
        # 웹검색 경로는 스트리밍을 쓴다(비스트리밍 10분 상한 회피) — sync 호출로
        # 컨텍스트 매니저를 돌려주는 SDK 계약 그대로.
        self.calls.append(kwargs)
        return _FakeStream(self._results[len(self.calls) - 1])


class _FakeClient:
    def __init__(self, results: list[Any]) -> None:
        self.messages = _FakeMessages(results)


class TestCallProviderCaching:
    @pytest.mark.asyncio
    async def test_system_sent_as_cacheable_block(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        fake = _FakeClient(
            [
                SimpleNamespace(
                    content=[_text_block("답변")],
                    usage=_usage(cache_read_input_tokens=40, cache_creation_input_tokens=60),
                    model="claude-sonnet-4-6",
                    stop_reason="end_turn",
                )
            ]
        )
        adapter._client = fake

        request = CompletionRequest(
            messages=[Message(role="user", content="질문")],
            model="claude-sonnet-4-6",
            system="페르소나 시스템 프롬프트",
        )
        response = await adapter._call_provider(request)

        kwargs = fake.messages.calls[0]
        assert kwargs["system"] == [
            {
                "type": "text",
                "text": "페르소나 시스템 프롬프트",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        # 단발 경로는 top-level 자동 캐싱을 쓰지 않는다 — 후보 2개가 병렬이라
        # 전체 프롬프트 캐싱은 읽기 없이 쓰기 프리미엄만 내는 손해 경로다.
        assert "cache_control" not in kwargs
        assert response.cached_input_tokens == 40
        assert response.cache_write_input_tokens == 60

    @pytest.mark.asyncio
    async def test_cache_prefix_messages_marks_boundary(self, tmp_path: Path) -> None:
        """분할 생성 계약: [프리픽스, tail] 2메시지 + cache_prefix_messages=1이면
        프리픽스 끝에 브레이크포인트가 찍히고 tail은 문자열 그대로 남는다."""
        adapter = _make_adapter(tmp_path)
        fake = _FakeClient(
            [
                SimpleNamespace(
                    content=[_text_block("파트")],
                    usage=_usage(),
                    model="claude-sonnet-4-6",
                    stop_reason="end_turn",
                )
            ]
        )
        adapter._client = fake

        request = CompletionRequest(
            messages=[
                Message(role="user", content="공유 프리픽스(근거)"),
                Message(role="user", content="파트 지시"),
            ],
            model="claude-sonnet-4-6",
            cache_prefix_messages=1,
        )
        await adapter._call_provider(request)

        sent = fake.messages.calls[0]["messages"]
        assert sent[0]["content"] == [
            {
                "type": "text",
                "text": "공유 프리픽스(근거)",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert sent[1]["content"] == "파트 지시"

    @pytest.mark.asyncio
    async def test_no_system_no_block(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        fake = _FakeClient(
            [
                SimpleNamespace(
                    content=[_text_block("답변")],
                    usage=_usage(),
                    model="claude-haiku-4-5",
                    stop_reason="end_turn",
                )
            ]
        )
        adapter._client = fake

        request = CompletionRequest(
            messages=[Message(role="user", content="질문")], model="claude-haiku-4-5"
        )
        await adapter._call_provider(request)
        assert "system" not in fake.messages.calls[0]


class TestCassetteHashStability:
    def test_cache_prefix_hint_does_not_change_hash(self) -> None:
        """cache_prefix_messages는 전송 내용을 바꾸지 않는 힌트 — 해시에서 제외돼
        기존 카세트가 무효화되지 않아야 한다(같은 내용 = 같은 카세트)."""
        from src.clients.llm.cassette import compute_input_hash

        base = CompletionRequest(
            messages=[
                Message(role="user", content="프리픽스"),
                Message(role="user", content="tail"),
            ],
            model="claude-haiku-4-5",
        )
        hinted = base.model_copy(update={"cache_prefix_messages": 1})
        assert compute_input_hash(base) == compute_input_hash(hinted)


class TestWebSearchCaching:
    @pytest.mark.asyncio
    async def test_pause_turn_loop_caches_and_accumulates_writes(self, tmp_path: Path) -> None:
        adapter = _make_adapter(tmp_path)
        paused = SimpleNamespace(
            content=[_text_block("중간")],
            usage=_usage(cache_creation_input_tokens=1000),
            model="claude-sonnet-4-6",
            stop_reason="pause_turn",
        )
        done = SimpleNamespace(
            content=[_text_block("최종")],
            usage=_usage(cache_read_input_tokens=900, cache_creation_input_tokens=500),
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
        )
        fake = _FakeClient([paused, done])
        adapter._client = fake

        request = CompletionRequest(
            messages=[Message(role="user", content="수집 스펙")],
            model="claude-sonnet-4-6",
            system="리서치 시스템",
            web_search=WebSearchConfig(max_uses=3),
        )
        response = await adapter._call_provider(request)

        assert len(fake.messages.calls) == 2
        for kwargs in fake.messages.calls:
            assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

        # 경계는 대화 끝으로 따라간다 — 그래야 직전 턴까지의 회수 본문이 다음 턴의
        # 캐시 프리픽스가 된다. 옛 경계는 지운다(브레이크포인트 4개 상한).
        # (두 호출이 같은 리스트를 공유하므로 관찰 가능한 건 최종 상태다.)
        sent = fake.messages.calls[-1]["messages"]
        assert sent[0]["content"][0]["text"] == "수집 스펙"
        assert "cache_control" not in sent[0]["content"][0]
        assert sent[-1]["role"] == "assistant"
        assert sent[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

        assert response.cached_input_tokens == 900
        assert response.cache_write_input_tokens == 1500  # 턴 누적(1000+500)
        assert response.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_pause_turn_resend_carries_container_id(self, tmp_path: Path) -> None:
        """동적 필터링 웹도구(비-Haiku)는 서버가 code_execution 컨테이너에서 돈다.

        재전송에 컨테이너 id를 안 실으면 400 "container_id is required when there
        are pending tool uses"로 챕터 수집이 통째로 죽는다(2026-08-07 Sonnet 실측).
        """
        adapter = _make_adapter(tmp_path)
        paused = SimpleNamespace(
            content=[_text_block("중간")],
            usage=_usage(),
            model="claude-sonnet-4-6",
            stop_reason="pause_turn",
            container=SimpleNamespace(id="container_abc"),
        )
        done = SimpleNamespace(
            content=[_text_block("최종")],
            usage=_usage(),
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
            container=SimpleNamespace(id="container_abc"),
        )
        fake = _FakeClient([paused, done])
        adapter._client = fake

        request = CompletionRequest(
            messages=[Message(role="user", content="수집 스펙")],
            model="claude-sonnet-4-6",
            web_search=WebSearchConfig(max_uses=3),
        )
        await adapter._call_provider(request)

        # 첫 턴은 컨테이너가 없고(신규 생성), 재전송부터 같은 컨테이너를 승계한다
        assert "container" not in fake.messages.calls[0]
        assert fake.messages.calls[1]["container"] == "container_abc"

    @pytest.mark.asyncio
    async def test_web_search_uses_streaming(self, tmp_path: Path) -> None:
        """웹검색 경로는 스트리밍이어야 한다 — 비스트리밍 10분 상한을 넘기면
        APITimeoutError로 죽고 재시도 3회가 챕터당 30분을 태운다(2026-08-07 실측)."""
        adapter = _make_adapter(tmp_path)
        fake = _FakeClient(
            [
                SimpleNamespace(
                    content=[_text_block("최종")],
                    usage=_usage(),
                    model="claude-sonnet-4-6",
                    stop_reason="end_turn",
                    container=None,
                )
            ]
        )

        async def _boom(**_kwargs: Any) -> Any:
            raise AssertionError("웹검색 경로가 비스트리밍 create를 썼다")

        fake.messages.create = _boom  # type: ignore[method-assign]
        adapter._client = fake

        response = await adapter._call_provider(
            CompletionRequest(
                messages=[Message(role="user", content="수집 스펙")],
                model="claude-sonnet-4-6",
                web_search=WebSearchConfig(max_uses=3),
            )
        )
        assert response.content == "최종"
