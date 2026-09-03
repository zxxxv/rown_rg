"""0청크 자료 자동 제외(일괄 판정) + 죽은 런 신호(/progress runner_alive) 통합 테스트."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.services.indexing.exclusion import AUTO_EXCLUDED_KEY, auto_exclude_chunkless
from tests.conftest import auth_headers as _auth


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


class TestRunnerAliveSignal:
    async def test_progress_reports_dead_run(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        """작업 단계 + 게이트 없음 + 러너 없음 = 죽은 런 신호가 그대로 내려온다.

        프로세스 재시작·태스크 크래시 후 status가 작업 단계에 남는 실사고(2026-08-12,
        3시간 '진행 중' 스피너)를 프론트가 판별할 수 있게 하는 계약.
        """
        # 단계는 이제 **산출물에서 되짚는다**(2026-08-26). 아무것도 없는 프로젝트가
        # 'writing'이라 주장하면 그 값이 교정되므로, 죽은 런을 재현하려면 그 단계까지
        # 실제로 만들어진 흔적이 있어야 한다 - 색인된 청크가 있는데 러너가 없는 상태.
        project = await _make_project(test_session, super_admin_user.id, status="writing")
        await test_session.flush()
        test_session.add(
            Chunk(project_id=project.id, track="content", content="색인된 조각", chunk_index=0)
        )
        await test_session.commit()

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/progress", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "writing"
        assert body["pending_gate"] is None
        assert body["runner_alive"] is False  # 이 테스트 프로세스에 러너 태스크가 없다
        assert body["last_event_at"] is None  # 이벤트를 발행한 적 없음

    async def test_last_event_at_follows_emits(
        self,
        test_client: AsyncClient,
        super_admin_user,
        super_admin_token: str,
        test_session: AsyncSession,
    ) -> None:
        from src.workflows.events import emit_step

        project = await _make_project(test_session, super_admin_user.id, status="indexing")
        await test_session.commit()
        emit_step(project.id, "indexing", "청킹·임베딩 1/3", "started")

        resp = await test_client.get(
            f"/api/v1/projects/{project.id}/progress", headers=_auth(super_admin_token)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["last_event_at"] is not None
