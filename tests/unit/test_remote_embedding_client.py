"""원격 임베딩 클라이언트 — 검증·폴백·쿨다운·청크 분할.

리랭커 테스트와 지키려는 것이 다르다. 저쪽은 "망가져도 보고서 생성은 계속된다"였고,
여기는 **"이상한 벡터가 색인에 들어가지 않는다"**이다. 리랭커 점수는 저장되지 않아
틀려도 다음 런에서 회복되지만, 임베딩은 색인에 박혀서 되짚기가 매우 어렵다.
그래서 개수·차원·유한성을 통과 못 하면 저장하지 않고 폴백으로 보낸다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.clients.embedding_client import EmbeddingResult
from src.clients.onnx_text_embedder import DIMENSION
from src.clients.remote_embedding_client import FALLBACK_ERROR, RemoteEmbeddingClient


def _vec(fill: float = 0.1) -> list[float]:
    return [fill] * DIMENSION


class _FakeLocal:
    """로컬 폴백 모델 대역 — 호출 여부를 기록한다."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        self.calls.append(len(texts))
        return [EmbeddingResult(embedding=_vec(0.5), text=t, cached=False) for t in texts]


def _client(handler, *, fallback: str = "local", local=None, cooldown_s: float = 60.0, chunk: int = 256):
    transport = httpx.MockTransport(handler)
    return RemoteEmbeddingClient(
        base_url="http://gpu.local:8009",
        token="secret",
        timeout_s=5.0,
        connect_timeout_s=1.0,
        cooldown_s=cooldown_s,
        chunk=chunk,
        fallback=fallback,
        client=httpx.AsyncClient(transport=transport),
        local_factory=(lambda: local) if local is not None else None,
    )


class TestHappyPath:
    def test_returns_vectors_in_input_order(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/embed"
            assert request.headers["Authorization"] == "Bearer secret"
            return httpx.Response(
                200, json={"vectors": [_vec(0.1), _vec(0.2)], "dimension": DIMENSION}
            )

        client = _client(handler)
        out = asyncio.run(client.embed_batch(["a", "b"]))
        assert [r.text for r in out] == ["a", "b"]
        assert out[0].embedding[0] == pytest.approx(0.1)
        assert out[1].embedding[0] == pytest.approx(0.2)

    def test_empty_input_makes_no_request(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("빈 입력에는 요청이 나가면 안 된다")

        assert asyncio.run(_client(handler).embed_batch([])) == []

    def test_splits_into_chunks_and_preserves_order(self):
        """청크 분할이 순서를 흐트러뜨리지 않아야 한다 — 색인이 통째로 어긋나는 자리다."""
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            texts = json.loads(request.content)["texts"]
            seen.append(len(texts))
            # 입력 문자열을 그대로 벡터 첫 값에 실어 순서를 추적한다.
            return httpx.Response(
                200, json={"vectors": [_vec(float(t)) for t in texts], "dimension": DIMENSION}
            )

        client = _client(handler, chunk=2)
        out = asyncio.run(client.embed_batch(["1", "2", "3", "4", "5"]))
        assert seen == [2, 2, 1]
        assert [r.embedding[0] for r in out] == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_clamps_overlong_input_before_sending(self):
        """4,000자 상한은 보내기 전에 걸어야 한다 - 토크나이저가 터지는 것은 서버 쪽이다."""
        captured: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.extend(len(t) for t in json.loads(request.content)["texts"])
            return httpx.Response(200, json={"vectors": [_vec()], "dimension": DIMENSION})

        asyncio.run(_client(handler).embed_batch(["가" * 10_000]))
        assert captured == [4_000]


class TestValidation:
    """이상한 응답은 저장하지 않고 폴백으로 보낸다."""

    @pytest.mark.parametrize(
        ("payload", "why"),
        [
            ({"vectors": [_vec(), _vec()]}, "개수 초과"),
            ({"vectors": []}, "개수 부족"),
            ({"vectors": [[0.1] * (DIMENSION - 1)]}, "차원 부족"),
            ({"vectors": [[0.1] * (DIMENSION + 1)]}, "차원 초과"),
            ({"vectors": [["x"] * DIMENSION]}, "수가 아닌 값"),
            ({"scores": [0.1]}, "vectors 키 없음"),
            ([], "객체가 아님"),
        ],
    )
    def test_bad_payload_falls_back(self, payload, why):
        local = _FakeLocal()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        out = asyncio.run(_client(handler, local=local).embed_batch(["a"]))
        assert local.calls == [1], f"{why}에서 폴백하지 않았다"
        assert out[0].embedding[0] == pytest.approx(0.5)

    def test_nan_falls_back(self):
        """NaN 한 건이 섞이면 그 벡터와의 유사도 계산이 전부 오염된다."""
        local = _FakeLocal()

        def handler(request: httpx.Request) -> httpx.Response:
            # 표준 json 모듈은 NaN을 그대로 내보낸다(엄격한 JSON은 아니지만 파서는 받는다).
            # 실제 서비스가 그런 응답을 낼 수 있으므로 본문을 직접 만들어 재현한다.
            body = "{\"vectors\": [[NaN" + ", 0.1" * (DIMENSION - 1) + "]]}"
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        out = asyncio.run(_client(handler, local=local).embed_batch(["a"]))
        assert local.calls == [1], "NaN 응답을 그대로 통과시켰다"
        assert out[0].embedding[0] == pytest.approx(0.5)


class TestFallback:
    def test_http_error_falls_back_to_local(self):
        local = _FakeLocal()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        out = asyncio.run(_client(handler, local=local).embed_batch(["a", "b"]))
        assert local.calls == [2]
        assert len(out) == 2

    def test_error_mode_raises_instead_of_local(self):
        """fallback=error는 dtype이 다른 벡터가 색인에 들어가는 것을 막는다."""
        local = _FakeLocal()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = _client(handler, fallback=FALLBACK_ERROR, local=local)
        with pytest.raises(RuntimeError, match="원격 임베딩 실패"):
            asyncio.run(client.embed_batch(["a"]))
        assert local.calls == [], "error 모드인데 로컬을 불렀다"

    def test_local_model_not_built_until_first_failure(self):
        """지연 생성이 깨지면 서버가 평소에도 2GB 모델을 물고 있게 된다."""
        built: list[int] = []

        def factory():
            built.append(1)
            return _FakeLocal()

        def ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"vectors": [_vec()], "dimension": DIMENSION})

        transport = httpx.MockTransport(ok)
        client = RemoteEmbeddingClient(
            base_url="http://gpu.local:8009",
            token="",
            chunk=256,
            fallback="local",
            client=httpx.AsyncClient(transport=transport),
            local_factory=factory,
        )
        asyncio.run(client.embed_batch(["a"]))
        assert built == [], "성공 경로인데 로컬 모델을 만들었다"


class TestCooldown:
    def test_second_call_skips_remote_during_cooldown(self):
        """색인은 수천 번 호출한다 - 쿨다운이 없으면 죽은 서비스를 수천 번 기다린다."""
        local = _FakeLocal()
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503)

        client = _client(handler, local=local, cooldown_s=60.0)
        asyncio.run(client.embed_batch(["a"]))
        asyncio.run(client.embed_batch(["b"]))
        assert attempts == [1], "쿨다운 중인데 원격을 다시 불렀다"
        assert local.calls == [1, 1]

    def test_retries_remote_after_cooldown_expires(self):
        local = _FakeLocal()
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503)

        client = _client(handler, local=local, cooldown_s=0.0)
        asyncio.run(client.embed_batch(["a"]))
        asyncio.run(client.embed_batch(["b"]))
        assert attempts == [1, 1], "쿨다운이 끝났는데 원격을 건너뛰었다"
