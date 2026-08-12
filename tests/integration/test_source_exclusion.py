"""0청크 자료 자동 제외(일괄 판정) 통합 테스트."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.services.indexing.exclusion import AUTO_EXCLUDED_KEY, auto_exclude_chunkless


async def _make_project(session: AsyncSession, owner_id, status: str = "indexing") -> Project:
    project = Project(
        title="제외 테스트",
        topic="AI 반도체",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status=status,
    )
    session.add(project)
    await session.flush()
    return project


class TestAutoExcludeChunkless:
    async def test_only_chunkless_sources_excluded(
        self, super_admin_user, test_session: AsyncSession
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id)
        with_chunk = ProjectSource(
            project_id=project.id, source_type="web_search", title="실자료", url="https://a.kr"
        )
        empty = ProjectSource(
            project_id=project.id, source_type="web_search", title="빈자료", url="https://b.kr"
        )
        test_session.add_all([with_chunk, empty])
        await test_session.flush()
        test_session.add(
            Chunk(project_id=project.id, source_id=with_chunk.id, track="content", content="본문")
        )
        await test_session.commit()

        kept_id, dropped_id = with_chunk.id, empty.id
        # 호출자가 둘 다 '색인 안 됨'으로 넘겨도 DB 청크 수를 다시 세서 실제 0인 것만 제외
        n = await auto_exclude_chunkless(project.id, [kept_id, dropped_id])
        assert n == 1

        test_session.expire_all()
        kept = await test_session.get(ProjectSource, kept_id)
        dropped = await test_session.get(ProjectSource, dropped_id)
        assert kept.is_included is True
        assert dropped.is_included is False
        assert dropped.metadata_[AUTO_EXCLUDED_KEY] is True
        assert dropped.metadata_["index_error"]  # 목록에서 이유가 보인다
