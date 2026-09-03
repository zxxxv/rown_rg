"""단계 파생이 실제 요청 경로에서 성립하는가.

멈춘 프로젝트가 "AI가 자료를 검색하고 있습니다"를 스피너와 함께 띄우던 사고(2026-08-25)의
회귀 방지. 그때는 게이트를 함께 여는 것으로 덮었는데, 이제는 값 자체가 정직해야 한다.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.types import ProjectStage
from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User
from tests.conftest import auth_headers as _auth


async def _project_with_body(
    session: AsyncSession, owner_id: uuid.UUID, *, status: str, completed: bool = False
) -> uuid.UUID:
    from src.core.clock import now as clock_now

    proj = Project(
        title="단계 파생",
        topic="주제",
        config={},
        status=status,
        depth_mode="full_report",
        owner_id=owner_id,
        completed_at=clock_now() if completed else None,
    )
    session.add(proj)
    await session.flush()
    session.add(
        Section(
            id=uuid.uuid4(),
            project_id=proj.id,
            chapter_number=1,
            section_number=1,
            chapter_title="1장",
            title="배경",
            content="본문이 이미 쓰여 있다.",
            source_ids=[],
            status="completed",
        )
    )
    await session.commit()
    return proj.id


class TestStageDerivation:
    async def test_detail_read_heals_a_lying_column(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """본문이 다 쓰인 프로젝트가 'researching'으로 남아 있으면 열어 보는 순간 낫는다.

        운영 DB 실측(2026-08-26)에서 8건 중 3건이 이 상태였다 - 목록에서 "자료 검색 중"
        으로 보이고 스피너가 끝나지 않는다.
        """
        pid = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.RESEARCHING.value
        )

        body = (
            await test_client.get(f"/api/v1/projects/{pid}", headers=_auth(worker_token))
        ).json()
        assert body["status"] == ProjectStage.REVIEWING.value

        # 컬럼도 함께 새겨진다 - 목록 필터(SQL)가 같은 답을 해야 한다.
        row = await test_session.get(Project, pid)
        await test_session.refresh(row)
        assert row.status == ProjectStage.REVIEWING.value

    async def test_reopen_lands_on_review_not_research(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """다시 열기는 '자료 수집 중'이 아니라 '검토 차례'다.

        예전엔 RESEARCHING을 박아 넣어 화면이 "수집 실행 중"으로 읽었고, 그걸 게이트를
        함께 여는 것으로 덮었다. 이제 값 자체가 정직하다(게이트는 그대로 열린다 -
        사람이 할 일 목록이라는 제 몫이 있다).
        """
        pid = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.COMPLETED.value, completed=True
        )

        resp = await test_client.post(f"/api/v1/projects/{pid}/reopen", headers=_auth(worker_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == ProjectStage.REVIEWING.value

        row = await test_session.get(Project, pid)
        await test_session.refresh(row)
        assert row.status == ProjectStage.REVIEWING.value
        assert row.completed_at is None
        assert row.finalized_at is None
        # 파이프라인 재개 지점은 **따로** 적힌다. status(REVIEWING)를 그대로 쓰면
        # '이어서 진행'이 조립로 직행해 새로 올린 자료의 색인·작성을 건너뛴다.
        assert row.config.get("resume_from") == ProjectStage.RESEARCHING.value

    async def test_completed_stays_completed(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """확정한 보고서를 열어 봤다고 단계가 내려가면 안 된다."""
        pid = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.COMPLETED.value, completed=True
        )
        body = (
            await test_client.get(f"/api/v1/projects/{pid}", headers=_auth(worker_token))
        ).json()
        assert body["status"] == ProjectStage.COMPLETED.value

    async def test_cancelled_is_not_rewritten_by_artifacts(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """취소는 사람이 만든 사실이라 산출물이 덮지 못한다."""
        pid = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.CANCELLED.value
        )
        body = (
            await test_client.get(f"/api/v1/projects/{pid}", headers=_auth(worker_token))
        ).json()
        assert body["status"] == ProjectStage.CANCELLED.value

    async def test_in_progress_list_shows_a_reopened_report(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        """컬럼을 남겨 둔 유일한 이유가 목록 필터다 - 파생값과 같은 답을 해야 한다."""
        pid = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.COMPLETED.value, completed=True
        )
        await test_client.post(f"/api/v1/projects/{pid}/reopen", headers=_auth(worker_token))

        listed = (
            await test_client.get(
                "/api/v1/projects?status=in_progress", headers=_auth(worker_token)
            )
        ).json()
        items = listed["items"] if isinstance(listed, dict) else listed
        assert any(str(p["id"]) == str(pid) for p in items), "다시 연 보고서가 진행 중에 없다"


class TestCompletedMeansFinalized:
    """'완료' 칸은 **최종 확정된 것만** 보여준다(2026-08-26 결정).

    파이프라인 완주는 사이클이 끝난 것일 뿐이다. 사람이 확정 버튼을 누르기 전까지는
    자료를 넣고 절을 고치며 살아 있는 문서라, 완료 칸에 두면 "다 된 것"으로 읽힌다.
    """

    async def _ids(self, client: AsyncClient, token: str, status: str) -> set[str]:
        resp = await client.get(f"/api/v1/projects?status={status}", headers=_auth(token))
        body = resp.json()
        items = body["items"] if isinstance(body, dict) else body
        return {str(p["id"]) for p in items}

    async def test_unfinalized_report_sits_in_progress_not_completed(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ):
        from src.core.clock import now as clock_now

        unsigned = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.COMPLETED.value, completed=True
        )
        signed = await _project_with_body(
            test_session, worker_user.id, status=ProjectStage.COMPLETED.value, completed=True
        )
        row = await test_session.get(Project, signed)
        row.finalized_at = clock_now()
        await test_session.commit()

        done = await self._ids(test_client, worker_token, "completed")
        doing = await self._ids(test_client, worker_token, "in_progress")

        assert str(signed) in done, "확정본은 완료 칸에 있어야 한다"
        assert str(unsigned) not in done, "확정 전인데 완료 칸에 있다"
        assert str(unsigned) in doing, "확정 전 보고서가 어느 칸에도 없다"
        assert str(signed) not in doing
