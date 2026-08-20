"""원격 리랭커 클라이언트 — 폴백·검증·쿨다운.

여기서 지키려는 것은 하나다: **원격이 어떤 식으로 망가져도 보고서 생성은 계속된다.**
개인 PC에 매달린 서비스라 정전·윈도우 업데이트·터널 끊김이 실제로 온다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.clients.remote_reranker_client import (
    FALLBACK_PASSTHROUGH,
    RemoteRerankerClient,
)


class _FakeLocal:
    """로컬 폴백 모델 대역 — 호출 여부를 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def score_pairs(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, len(passages)))
        return [0.42] * len(passages)


def _client(handler, *, fallback: str = "local", local=None, cooldown_s: float = 60.0):
    transport = httpx.MockTransport(handler)
    return RemoteRerankerClient(
        base_url="http://gpu.local:8009",
        token="secret",
        timeout_s=5.0,
        connect_timeout_s=1.0,
        cooldown_s=cooldown_s,
        fallback=fallback,
        client=httpx.AsyncClient(transport=transport),
        local_factory=(lambda: local) if local is not None else None,
    )


class TestHappyPath:
    def test_returns_remote_scores_in_order(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/rerank"
            assert request.headers["Authorization"] == "Bearer secret"
            return httpx.Response(200, json={"scores": [0.9, 0.1, 0.5]})

        client = _client(handler)
        result = asyncio.run(client.score_pairs("q", ["a", "b", "c"]))
        assert result == [0.9, 0.1, 0.5]

    def test_empty_passages_never_calls_remote(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("빈 입력에 원격을 부르면 안 된다")

        client = _client(handler)
        assert asyncio.run(client.score_pairs("q", [])) == []

    def test_sends_query_and_passages(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"scores": [0.5, 0.5]})

        client = _client(handler)
        asyncio.run(client.score_pairs("질의", ["문단1", "문단2"]))
        assert seen == {"query": "질의", "passages": ["문단1", "문단2"]}


class TestFallsBackOnFailure:
    """원격이 망가지는 방식마다 폴백이 도는지."""

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, text="boom"),
            httpx.Response(401, json={"detail": "invalid token"}),
            # 개수 불일치 — rerank_hits의 zip(strict=True)가 터지는 자리라 여기서 막아야 한다.
            httpx.Response(200, json={"scores": [0.1, 0.2]}),
            httpx.Response(200, json={"scores": "not-a-list"}),
            httpx.Response(200, json={"nope": 1}),
            # [0,1] 밖 — 시그모이드를 안 거친 raw logit이 새어 나온 경우
            httpx.Response(200, json={"scores": [0.5, 1.7, 0.3]}),
            httpx.Response(200, json={"scores": [0.5, None, 0.3]}),
        ],
        ids=["500", "401", "짧은배열", "배열아님", "필드없음", "범위밖", "None"],
    )
    def test_broken_response_uses_local_fallback(self, response):
        local = _FakeLocal()
        client = _client(lambda request: response, local=local)
        result = asyncio.run(client.score_pairs("q", ["a", "b", "c"]))
        assert result == [0.42, 0.42, 0.42]
        assert local.calls == [("q", 3)]

    def test_transport_error_uses_local_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        local = _FakeLocal()
        client = _client(handler, local=local)
        result = asyncio.run(client.score_pairs("q", ["a"]))
        assert result == [0.42]
        assert local.calls == [("q", 1)]


class TestCooldown:
    def test_second_call_skips_remote_after_failure(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("down")

        local = _FakeLocal()
        client = _client(handler, local=local)
        asyncio.run(client.score_pairs("q", ["a"]))
        asyncio.run(client.score_pairs("q", ["b"]))
        asyncio.run(client.score_pairs("q", ["c"]))
        # 20절 × 타임아웃을 죽은 서비스에 버리지 않는다 — 첫 실패 뒤로는 바로 폴백.
        assert attempts == 1
        assert len(local.calls) == 3

    def test_cooldown_expires_and_remote_is_retried(self):
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"scores": [0.7]})

        local = _FakeLocal()
        client = _client(handler, local=local, cooldown_s=0.0)
        assert asyncio.run(client.score_pairs("q", ["a"])) == [0.42]
        assert asyncio.run(client.score_pairs("q", ["b"])) == [0.7]
        assert attempts == 2


class TestPassthroughFallback:
    """모델 없는 서버를 만들려면 이 폴백이 '리랭킹만 없던 것'처럼 굴어야 한다."""

    def test_scores_are_strictly_descending(self):
        client = _client(lambda request: httpx.Response(500), fallback=FALLBACK_PASSTHROUGH)
        result = asyncio.run(client.score_pairs("q", [f"p{i}" for i in range(50)]))
        assert len(result) == 50
        # 엄격한 내림차순이라 rerank_hits의 정렬이 검색 순서를 그대로 보존한다.
        assert all(result[i] > result[i + 1] for i in range(49))

    def test_all_scores_clear_the_ratio_floor(self):
        from src.core.config import settings

        client = _client(lambda request: httpx.Response(500), fallback=FALLBACK_PASSTHROUGH)
        result = asyncio.run(client.score_pairs("q", [f"p{i}" for i in range(2000)]))
        # select_relevant는 1위 × retrieval_score_ratio 아래를 버린다.
        # 여기서 잘리면 폴백이 근거를 굶긴다 - 개수가 아무리 많아도 전원 통과해야 한다.
        floor = result[0] * settings.retrieval_score_ratio
        assert all(s >= floor for s in result)
        assert all(0.0 <= s <= 1.0 for s in result)

    def test_never_loads_local_model(self):
        def boom():
            raise AssertionError("passthrough 폴백이 로컬 모델을 올리면 안 된다")

        client = RemoteRerankerClient(
            base_url="http://gpu.local:8009",
            token="",
            timeout_s=1.0,
            connect_timeout_s=1.0,
            cooldown_s=60.0,
            fallback=FALLBACK_PASSTHROUGH,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(503))
            ),
            local_factory=boom,
        )
        assert len(asyncio.run(client.score_pairs("q", ["a", "b"]))) == 2
