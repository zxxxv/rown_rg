"""SemanticSearchClient tests — pgvector cosine search, embedder mocked."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.clients.embedding_client import EmbeddingResult
from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from src.infrastructure.auth.password_handler import hash_password
from src.services.retrieval._semantic import SemanticSearchClient

pytestmark = pytest.mark.asyncio

DIM = 1024


def _unit_vec(seed: int) -> list[float]:
    """Deterministic L2-normalized vector for testing."""
    raw = [math.sin(seed * (i + 1)) for i in range(DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def _make_embedder(seed: int = 1) -> MagicMock:
    embedder = MagicMock()
    embedder.embed = AsyncMock(
        return_value=EmbeddingResult(embedding=_unit_vec(seed), text="q", cached=False)
    )
    return embedder


async def _seed_project(session: AsyncSession) -> Project:
    user = User(
        email=f"sem-{uuid4().hex[:6]}@test.com",
        name="sem",
        role="worker",
        password_hash=hash_password("Sem12345678!@"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    project = Project(title="sem-project", topic="topic", owner_id=user.id)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def _seed_source(session: AsyncSession, project: Project) -> ProjectSource:
    node = LibraryNode(name="lib.hwpx", type="file", file_path="/lib/lib.hwpx")
    session.add(node)
    await session.flush()
    src = ProjectSource(
        project_id=project.id,
        library_node_id=node.id,
        source_type="library",
        title="seed",
    )
    session.add(src)
    await session.commit()
    await session.refresh(src)
    return src


async def _seed_corpus(session: AsyncSession, test_session_maker) -> dict:
    project_a = await _seed_project(session)
    project_b = await _seed_project(session)
    source_a = await _seed_source(session, project_a)
    source_b = await _seed_source(session, project_b)

    # 시드 1과 가까운 청크가 정렬 상위로 잡혀야 한다 — seed 1 ~ 5는 점진적으로 멀어짐.
    chunks_data = [
        (project_a.id, source_a.id, "가까운 청크 1", _unit_vec(1), 0, "content"),
        (project_a.id, source_a.id, "조금 먼 청크 2", _unit_vec(2), 1, "content"),
        (project_a.id, source_a.id, "더 먼 청크 3", _unit_vec(3), 2, "content"),
        (project_a.id, source_a.id, "가장 먼 청크 4", _unit_vec(10), 3, "content"),
        # 다른 트랙 — content 검색에 나오면 안 됨.
        (project_a.id, source_a.id, "style 청크", _unit_vec(1), 4, "style"),
        # 다른 프로젝트 — project_a 검색에 나오면 안 됨.
        (project_b.id, source_b.id, "다른 프로젝트 청크", _unit_vec(1), 0, "content"),
    ]
    for pid, sid, content, vec, idx, track in chunks_data:
        async with test_session_maker() as s:
            s.add(
                ChunkModel(
                    project_id=pid,
                    source_id=sid,
                    track=track,
                    content=content,
                    embedding=vec,
                    chunk_index=idx,
                    metadata_={"chunk_type": "text"},
                )
            )
            await s.commit()
    return {
        "project_a": project_a,
        "project_b": project_b,
        "source_a": source_a,
        "source_b": source_b,
    }


class TestSemanticSearchPositive:
    async def test_embedder_called_and_sql_runs(
        self, test_session: AsyncSession, test_session_maker
    ):
        data = await _seed_corpus(test_session, test_session_maker)
        embedder = _make_embedder(seed=1)
        client = SemanticSearchClient(test_session_maker, embedder)
        hits = await client.search("쿼리", data["project_a"].id)
        embedder.embed.assert_awaited_once_with("쿼리")
        assert hits

    async def test_results_sorted_by_cosine_similarity_desc(
        self, test_session: AsyncSession, test_session_maker
    ):
        data = await _seed_corpus(test_session, test_session_maker)
        client = SemanticSearchClient(test_session_maker, _make_embedder(seed=1))
        hits = await client.search("쿼리", data["project_a"].id, top_k=4)
        # seed=1 쿼리에 대해 seed=1 청크가 최상위, seed=10 청크가 최하위 (코사인 정렬).
        assert hits[0].content == "가까운 청크 1"
        # 점수 내림차순 보장.
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    async def test_project_id_filter(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = SemanticSearchClient(test_session_maker, _make_embedder(seed=1))
        hits = await client.search("쿼리", data["project_a"].id)
        # project_b 청크의 content "다른 프로젝트 청크"가 결과에 들어가면 필터 깨진 것.
        assert not any("다른 프로젝트" in h.content for h in hits)

    async def test_track_filter(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = SemanticSearchClient(test_session_maker, _make_embedder(seed=1))
        hits = await client.search("쿼리", data["project_a"].id, track="content")
        assert not any("style" in h.content for h in hits)

    async def test_top_k_limit(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = SemanticSearchClient(test_session_maker, _make_embedder(seed=1))
        hits = await client.search("쿼리", data["project_a"].id, top_k=2)
        assert len(hits) == 2

    async def test_score_in_unit_range(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = SemanticSearchClient(test_session_maker, _make_embedder(seed=1))
        hits = await client.search("쿼리", data["project_a"].id)
        for h in hits:
            # 정규화 임베딩의 코사인 유사도는 [-1, 1]. 작은 부동 오차 허용.
            assert -1.001 <= h.score <= 1.001

    async def test_score_source_labeled(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = SemanticSearchClient(test_session_maker, _make_embedder(seed=1))
        hits = await client.search("쿼리", data["project_a"].id)
        assert {h.score_source for h in hits} == {"semantic"}


class TestSemanticSearchNegative:
    async def test_empty_query_returns_empty(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        embedder = _make_embedder()
        client = SemanticSearchClient(test_session_maker, embedder)
        assert await client.search("", data["project_a"].id) == []
        # 빈 쿼리는 embedder 호출도 건너뛴다.
        embedder.embed.assert_not_awaited()

    async def test_empty_project_returns_empty(
        self, test_session: AsyncSession, test_session_maker
    ):
        # 코퍼스 없이 새 프로젝트 검색 — 매칭 0건.
        project = await _seed_project(test_session)
        client = SemanticSearchClient(test_session_maker, _make_embedder())
        assert await client.search("쿼리", project.id) == []

    async def test_embedder_failure_propagates(
        self, test_session: AsyncSession, test_session_maker
    ):
        data = await _seed_corpus(test_session, test_session_maker)
        embedder = MagicMock()
        embedder.embed = AsyncMock(side_effect=RuntimeError("embed boom"))
        client = SemanticSearchClient(test_session_maker, embedder)
        with pytest.raises(RuntimeError, match="embed boom"):
            await client.search("쿼리", data["project_a"].id)
