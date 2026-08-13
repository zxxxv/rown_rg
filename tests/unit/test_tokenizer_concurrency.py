"""공유 토크나이저 동시 호출 방지 — 실전에서 파이프라인을 멈춘 결함의 회귀 테스트.

2026-08-12: 병렬화를 넣은 뒤 색인이 죽었다.

    RuntimeError: Already borrowed
      _chunking._estimate_tokens → tokenizer.encode → set_truncation_and_padding

HF fast tokenizer는 Rust 백엔드라 하나의 인스턴스를 여러 스레드가 동시에 부르면
내부 상태를 가변 차용하다 충돌한다. 임베딩은 asyncio.to_thread로 나가고 청킹은
이벤트 루프에서 **같은** 토크나이저를 부르므로 실제로 겹쳤다. 자료 41개 중 4개만
색인되고 런이 3시간 넘게 멈춘 채 서버가 29GB를 붙들었다.

여기서 검사하는 것은 '락이 있는가'가 아니라 '동시 호출이 실제로 직렬화되는가'다.
락 객체 존재만 보면 청킹이 자기 락을 새로 만드는 실수를 못 잡는다 - 그러면 락은
있는데 직렬화는 안 된다.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest


class _BorrowCheckingTokenizer:
    """동시 호출을 실제로 잡아내는 토크나이저 흉내.

    진짜 Rust 토크나이저처럼, 이미 사용 중일 때 또 들어오면 예외를 던진다.
    """

    def __init__(self) -> None:
        self._busy = False
        self._guard = threading.Lock()
        self.max_concurrent = 0
        self._in_flight = 0

    def _enter(self) -> None:
        with self._guard:
            self._in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._in_flight)
            if self._busy:
                self._in_flight -= 1
                raise RuntimeError("Already borrowed")
            self._busy = True

    def _exit(self) -> None:
        with self._guard:
            self._busy = False
            self._in_flight -= 1

    def encode(self, text: str, **_kw: Any) -> list[int]:
        self._enter()
        try:
            time.sleep(0.01)  # 겹칠 시간을 준다
            return [0] * len(text.split())
        finally:
            self._exit()

    def __call__(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
        self._enter()
        try:
            time.sleep(0.01)
            import numpy as np

            return {
                "input_ids": np.zeros((1, 4), dtype=np.int64),
                "attention_mask": np.ones((1, 4), dtype=np.int64),
            }
        finally:
            self._exit()


class _FakeEmbeddingClient:
    """토크나이저와 락을 노출하는 최소 임베딩 클라이언트."""

    def __init__(self, tokenizer: Any) -> None:
        self._tokenizer = tokenizer
        self._tokenizer_lock = threading.Lock()

    @property
    def tokenizer(self) -> Any:
        return self._tokenizer

    @property
    def tokenizer_lock(self) -> threading.Lock:
        return self._tokenizer_lock


def test_청킹이_임베딩과_같은_락을_잡는다() -> None:
    """새 락을 만들면 직렬화가 성립하지 않는다 - 이 테스트가 그걸 잡는다."""
    from src.services.indexing._chunking import ChunkingService

    embedder = _FakeEmbeddingClient(_BorrowCheckingTokenizer())
    service = ChunkingService(embedder)
    assert service._tokenizer_lock is embedder.tokenizer_lock


def _sample_chunk(service, text: str):
    from uuid import uuid4

    return service._make_chunk(
        content=text, chunk_type="text", header_path=[], source_id=uuid4(), chunk_index=0
    )


def test_토큰_추정을_동시에_불러도_안_터진다() -> None:
    """락이 없거나 서로 다른 락이면 'Already borrowed'가 난다."""
    from src.services.indexing._chunking import ChunkingService

    tokenizer = _BorrowCheckingTokenizer()
    embedder = _FakeEmbeddingClient(tokenizer)
    service = ChunkingService(embedder)

    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(20):
                service._fill_token_estimates(
                    [_sample_chunk(service, "숏폼 콘텐츠 시장은 빠르게 성장하고 있다")]
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"동시 호출이 직렬화되지 않았다: {errors[:2]}"


def test_임베딩과_청킹이_동시에_돌아도_안_터진다() -> None:
    """실제 사고 조건 - 임베딩은 스레드에서, 청킹은 다른 스레드에서 같은 토크나이저를 쓴다."""
    from src.services.indexing._chunking import ChunkingService

    tokenizer = _BorrowCheckingTokenizer()
    embedder = _FakeEmbeddingClient(tokenizer)
    service = ChunkingService(embedder)
    errors: list[Exception] = []

    def chunker() -> None:
        try:
            for _ in range(20):
                service._fill_token_estimates(
                    [_sample_chunk(service, "본문 텍스트가 여기에 들어간다")]
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def embedder_worker() -> None:
        try:
            for _ in range(20):
                with embedder.tokenizer_lock:
                    tokenizer(["본문"], padding=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=chunker) for _ in range(3)]
    threads += [threading.Thread(target=embedder_worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"임베딩·청킹 동시 호출이 충돌했다: {errors[:2]}"


@pytest.mark.asyncio
async def test_리랭커도_동시_호출을_직렬화한다() -> None:
    import asyncio

    from src.clients.reranker_client import BgeRerankerV2M3Client

    client = object.__new__(BgeRerankerV2M3Client)
    tokenizer = _BorrowCheckingTokenizer()
    client._tokenizer = tokenizer
    client._tokenizer_lock = threading.Lock()
    client._max_length = 512

    class _Session:
        @staticmethod
        def run(_outputs: Any, _inputs: Any) -> list[Any]:
            import numpy as np

            return [np.zeros((1, 1), dtype=np.float32)]

    client._session = _Session()

    async def one() -> None:
        await asyncio.to_thread(client._score_batch, "질의", ["근거"])

    await asyncio.gather(*[one() for _ in range(8)])
