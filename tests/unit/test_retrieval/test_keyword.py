"""KeywordSearchClient tests — pgroonga-backed positive + negative + mutation surface."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from src.infrastructure.auth.password_handler import hash_password
from src.services.retrieval._keyword import KeywordSearchClient

pytestmark = pytest.mark.asyncio


async def _seed_project(session: AsyncSession) -> Project:
    user = User(
        email=f"kw-{uuid4().hex[:6]}@test.com",
        name="kw",
        role="worker",
        password_hash=hash_password("Kw12345678!@"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    project = Project(title="kw-project", topic="topic", owner_id=user.id)
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


async def _insert_chunk(
    session: AsyncSession,
    *,
    project_id,
    source_id,
    content: str,
    chunk_index: int,
    track: str = "content",
) -> ChunkModel:
    chunk = ChunkModel(
        project_id=project_id,
        source_id=source_id,
        track=track,
        content=content,
        embedding=[0.0] * 1024,
        chunk_index=chunk_index,
        metadata_={"chunk_type": "text"},
    )
    session.add(chunk)
    await session.commit()
    await session.refresh(chunk)
    return chunk


async def _seed_corpus(session: AsyncSession, test_session_maker) -> dict:
    """Insert a small Korean+English mixed corpus and return id handles."""
    project_a = await _seed_project(session)
    project_b = await _seed_project(session)
    source_a = await _seed_source(session, project_a)
    source_b = await _seed_source(session, project_b)

    chunks_data = [
        # project_a, content 트랙
        (project_a.id, source_a.id, "비용편익 분석은 ABC 방법론을 따른다.", 0, "content"),
        (project_a.id, source_a.id, "사업의 경제성을 분석한다.", 1, "content"),
        (project_a.id, source_a.id, "사업을 진행한다.", 2, "content"),
        (project_a.id, source_a.id, "SOC 투자 효과는 GDP 대비 1.32%로 측정됐다.", 3, "content"),
        (project_a.id, source_a.id, "정부의 정책 발표 이후 변동이 있었다.", 4, "content"),
        (project_a.id, source_a.id, "기타 일반 본문 청크 한 줄.", 5, "content"),
        # project_a, style track — content 검색에 나오면 안 됨
        (project_a.id, source_a.id, "사업 문체 트랙 청크.", 6, "style"),
        # project_b — 다른 프로젝트 — project_a 검색에 나오면 안 됨
        (project_b.id, source_b.id, "사업의 다른 프로젝트 청크.", 0, "content"),
    ]
    inserted = []
    for pid, sid, content, idx, track in chunks_data:
        async with test_session_maker() as s:
            ch = await _insert_chunk(
                s, project_id=pid, source_id=sid, content=content, chunk_index=idx, track=track
            )
            inserted.append(ch)
    return {
        "project_a": project_a,
        "project_b": project_b,
        "source_a": source_a,
        "source_b": source_b,
        "chunks": inserted,
    }


class TestKeywordSearchPositive:
    async def test_returns_matching_term(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)

        hits = await client.search("비용편익", data["project_a"].id)
        assert hits
        assert any("비용편익" in h.content for h in hits)
        assert {h.score_source for h in hits} == {"keyword"}

    async def test_particle_separation_korean(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)

        # "사업" 검색 → "사업의", "사업을" 둘 다 포함된 청크가 회수돼야 한다.
        hits = await client.search("사업", data["project_a"].id)
        contents = [h.content for h in hits]
        assert any("사업의" in c for c in contents)
        assert any("사업을" in c for c in contents)

    async def test_abbreviation_match_english(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        hits = await client.search("SOC", data["project_a"].id)
        assert hits
        assert any("SOC" in h.content for h in hits)

    async def test_project_id_filter_excludes_other_project(
        self, test_session: AsyncSession, test_session_maker
    ):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        hits = await client.search("사업", data["project_a"].id)
        for h in hits:
            assert h.source_id == data["source_a"].id

    async def test_track_filter_excludes_style_when_content_requested(
        self, test_session: AsyncSession, test_session_maker
    ):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        hits = await client.search("사업", data["project_a"].id, track="content")
        # style 트랙 청크의 content "사업 문체 트랙 청크." 가 결과에 들어가면 필터 깨진 것.
        assert not any("문체 트랙" in h.content for h in hits)

    async def test_score_descending_order(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        hits = await client.search("사업", data["project_a"].id)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    async def test_top_k_limit_applied(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        hits = await client.search("사업", data["project_a"].id, top_k=1)
        assert len(hits) <= 1


class TestKeywordSearchIsIncludedFilter:
    """자료 확정 게이트에서 제외된 출처(is_included=false)의 청크는 검색에서 빠진다."""

    async def _add_source(
        self, test_session_maker, project: Project, *, title: str, is_included: bool
    ) -> ProjectSource:
        async with test_session_maker() as s:
            node = LibraryNode(name=f"{title}.hwpx", type="file", file_path=f"/lib/{title}.hwpx")
            s.add(node)
            await s.flush()
            src = ProjectSource(
                project_id=project.id,
                library_node_id=node.id,
                source_type="library",
                title=title,
                is_included=is_included,
            )
            s.add(src)
            await s.commit()
            await s.refresh(src)
            return src

    async def test_excluded_filtered_included_returned(
        self, test_session: AsyncSession, test_session_maker
    ):
        data = await _seed_corpus(test_session, test_session_maker)
        project_a = data["project_a"]
        excluded = await self._add_source(
            test_session_maker, project_a, title="excluded", is_included=False
        )
        included = await self._add_source(
            test_session_maker, project_a, title="included", is_included=True
        )
        async with test_session_maker() as s:
            await _insert_chunk(
                s,
                project_id=project_a.id,
                source_id=excluded.id,
                content="제외 출처 고유어 ZZEXCLUDE 청크.",
                chunk_index=0,
            )
        async with test_session_maker() as s:
            await _insert_chunk(
                s,
                project_id=project_a.id,
                source_id=included.id,
                content="포함 출처 고유어 YYINCLUDE 청크.",
                chunk_index=0,
            )

        client = KeywordSearchClient(test_session_maker)
        # 제외 출처의 고유어는 회수되지 않는다.
        assert await client.search("ZZEXCLUDE", project_a.id) == []
        # 포함 출처의 고유어는 정상 회수된다(대조군).
        incl_hits = await client.search("YYINCLUDE", project_a.id)
        assert any("YYINCLUDE" in h.content for h in incl_hits)


class TestKeywordSearchNegative:
    async def test_empty_query_returns_empty(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        assert await client.search("", data["project_a"].id) == []
        assert await client.search("   ", data["project_a"].id) == []

    async def test_no_match_returns_empty(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        hits = await client.search("절대존재하지않는키워드xyz9999", data["project_a"].id)
        assert hits == []

    async def test_sql_injection_safely_bound(self, test_session: AsyncSession, test_session_maker):
        data = await _seed_corpus(test_session, test_session_maker)
        client = KeywordSearchClient(test_session_maker)
        # 매칭은 안 돼도 예외 없이 빈 list로 돌아와야 한다 — 파라미터 바인딩이 safe escape.
        hits = await client.search("'; DROP TABLE chunks; --", data["project_a"].id)
        assert hits == []

        # 후속 정상 검색이 작동 — 테이블이 살아있음을 확인.
        hits2 = await client.search("사업", data["project_a"].id)
        assert hits2
