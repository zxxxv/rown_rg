"""대목 벡터 보관 — 적재·조회·무효화.

근거 대조에서 대목을 볼 때마다 임베딩하면 절당 1.56초가 든다(실측 2026-08-27).
색인 때 한 번 만들어 두면 그 8할이 조회로 바뀐다. 여기서 지키는 계약은 셋이다:

  ① 넣은 대목이 (청크, 시작위치)로 그대로 나온다
  ② 청크 본문이 바뀌면 안 나온다 — 오프셋이 다른 글을 가리키게 되므로
  ③ 임베딩 공간이 다르면 안 나온다 — 거리가 뜻을 잃으므로

②③에서 "안 나온다"가 정답인 이유: 없으면 호출자가 그 자리에서 다시 만든다(느릴 뿐
맞는다). 반면 어긋난 벡터를 돌려주면 화면이 엉뚱한 대목을 "참고한 대목"으로 단정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.chunk_span_vector import ChunkSpanVector
from src.db.models.project import Project
from src.services.qa import span_vectors
from src.services.qa.alignment import _spans

CHUNK_TEXT = (
    "The global liquid biopsy market was valued at USD 7.05 billion in 2025.\n"
    "Korea remains one of the most challenging markets for RE100 implementation.\n"
    "Grand View Research expects the segment to grow at a CAGR of 24.12 percent.\n"
)


@dataclass
class _FakeResult:
    text: str
    embedding: list[float]
    cached: bool = False


class _FakeClient:
    """길이에 따라 결정적인 벡터를 준다 - 값 자체는 안 보고 짝만 본다."""

    def __init__(self) -> None:
        self.calls = 0

    async def embed_batch(self, texts: list[str]) -> list[_FakeResult]:
        self.calls += 1
        return [_FakeResult(t, [float(len(t) % 7 + 1)] + [0.0] * 1023) for t in texts]


async def _make_chunk(session: AsyncSession, owner_id, content: str = CHUNK_TEXT) -> Chunk:
    project = Project(
        title="대목 벡터",
        topic="근거 대조",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="indexing",
    )
    session.add(project)
    await session.flush()
    chunk = Chunk(
        project_id=project.id,
        track="content",
        content=content,
        embedding=[0.0] * 1024,
        chunk_index=0,
    )
    session.add(chunk)
    await session.flush()
    return chunk


class TestStoreAndLoad:
    async def test_roundtrip_keeps_every_span(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        chunk = await _make_chunk(test_session, super_admin_user.id)
        client = _FakeClient()

        n = await span_vectors.store_for_chunks(
            test_session, [(chunk.id, CHUNK_TEXT)], client=client
        )
        await test_session.commit()

        want = _spans(CHUNK_TEXT)
        assert n == len(want) > 0
        loaded = await span_vectors.load_for_chunks(test_session, {chunk.id: CHUNK_TEXT})
        assert set(loaded) == {(chunk.id, start) for start, _, _ in want}
        assert all(len(v) == 1024 for v in loaded.values())

    async def test_restore_replaces_instead_of_stacking(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        """다시 만들면 덮는다 - 세대가 쌓이면 조회가 어느 것을 줄지 알 수 없다."""
        chunk = await _make_chunk(test_session, super_admin_user.id)
        client = _FakeClient()
        for _ in range(3):
            await span_vectors.store_for_chunks(
                test_session, [(chunk.id, CHUNK_TEXT)], client=client
            )
        await test_session.commit()

        total = (
            await test_session.execute(
                select(func.count())
                .select_from(ChunkSpanVector)
                .where(ChunkSpanVector.chunk_id == chunk.id)
            )
        ).scalar_one()
        assert total == len(_spans(CHUNK_TEXT))

    async def test_empty_input_is_a_noop(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        client = _FakeClient()
        assert await span_vectors.store_for_chunks(test_session, [], client=client) == 0
        assert client.calls == 0
        assert await span_vectors.load_for_chunks(test_session, {}) == {}


class TestInvalidation:
    async def test_changed_content_is_not_served(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        """본문이 바뀌면 오프셋이 다른 글을 가리킨다 - 벡터보다 좌표가 먼저 죽는다."""
        chunk = await _make_chunk(test_session, super_admin_user.id)
        await span_vectors.store_for_chunks(
            test_session, [(chunk.id, CHUNK_TEXT)], client=_FakeClient()
        )
        await test_session.commit()

        edited = "머리말이 하나 붙었다.\n" + CHUNK_TEXT
        assert await span_vectors.load_for_chunks(test_session, {chunk.id: edited}) == {}
        # 원본으로 물으면 그대로 나온다 - 버린 게 아니라 안 쓴 것이다.
        assert await span_vectors.load_for_chunks(test_session, {chunk.id: CHUNK_TEXT})

    async def test_other_space_is_not_served(
        self, super_admin_user, test_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """모델이 바뀌면 옛 벡터는 다른 공간에 있다 - 섞으면 거리가 뜻을 잃는다."""
        chunk = await _make_chunk(test_session, super_admin_user.id)
        await span_vectors.store_for_chunks(
            test_session, [(chunk.id, CHUNK_TEXT)], client=_FakeClient()
        )
        await test_session.commit()

        monkeypatch.setattr(span_vectors, "space_id", lambda: "다른-모델")
        assert await span_vectors.load_for_chunks(test_session, {chunk.id: CHUNK_TEXT}) == {}


class TestStoreQuietly:
    async def test_embedding_failure_does_not_break_indexing(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        """대목 벡터는 있으면 좋은 것이지 자료가 들어오는 조건이 아니다."""

        class _Broken:
            async def embed_batch(self, texts):  # noqa: ANN001, ANN202
                raise RuntimeError("임베딩 서비스 순단")

        chunk = await _make_chunk(test_session, super_admin_user.id)
        await test_session.commit()

        def _maker():
            raise AssertionError("여기까지 오면 안 된다")

        # 세션을 못 열어도, 임베딩이 터져도 0을 돌려줄 뿐 예외가 새지 않는다.
        assert (
            await span_vectors.store_quietly(_maker, [(chunk.id, CHUNK_TEXT)], client=_Broken())
            == 0
        )


class TestSpaceId:
    def test_remote_and_local_are_different_spaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.core.config import settings

        monkeypatch.setattr(settings, "embedding_remote_url", "http://gpu:8009")
        assert span_vectors.space_id() == "remote"
        monkeypatch.setattr(settings, "embedding_remote_url", "")
        monkeypatch.setattr(settings, "embedding_model_path", "./models/bge-m3-onnx-int8")
        # dtype이 폴더 이름에 있으므로 int8/fp16이 갈린다.
        assert span_vectors.space_id() == "bge-m3-onnx-int8"
