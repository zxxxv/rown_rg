from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.types import ProjectStage
from src.db.models.project import Project
from src.db.models.user import User


async def _make_project(
    session: AsyncSession, owner: User, status: str = ProjectStage.CREATED.value
) -> Project:
    project = Project(
        title="테스트 리포트",
        topic="완료 시각 동기화 검증",
        owner_id=owner.id,
        status=status,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


class TestCompletedAtSync:
    async def test_transition_into_completed_records_timestamp(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        project = await _make_project(test_session, worker_user, status=ProjectStage.WRITING.value)
        assert project.completed_at is None

        project.status = ProjectStage.COMPLETED.value
        await test_session.commit()
        await test_session.refresh(project)

        assert project.completed_at is not None

    async def test_direct_insert_as_completed_records_timestamp(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        project = await _make_project(
            test_session, worker_user, status=ProjectStage.COMPLETED.value
        )

        assert project.completed_at is not None

    async def test_resaving_unrelated_field_keeps_completed_at_stable(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        project = await _make_project(
            test_session, worker_user, status=ProjectStage.COMPLETED.value
        )
        original_completed_at = project.completed_at
        assert original_completed_at is not None

        project.title = "제목 수정"
        await test_session.commit()
        await test_session.refresh(project)

        assert project.completed_at == original_completed_at

    async def test_resetting_status_to_completed_again_keeps_completed_at_stable(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        project = await _make_project(
            test_session, worker_user, status=ProjectStage.COMPLETED.value
        )
        original_completed_at = project.completed_at

        project.status = ProjectStage.COMPLETED.value
        await test_session.commit()
        await test_session.refresh(project)

        assert project.completed_at == original_completed_at

    async def test_transition_out_of_completed_resets_to_null(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        project = await _make_project(
            test_session, worker_user, status=ProjectStage.COMPLETED.value
        )
        assert project.completed_at is not None

        project.status = ProjectStage.WRITING.value
        await test_session.commit()
        await test_session.refresh(project)

        assert project.completed_at is None

    async def test_non_completed_transition_does_not_set_completed_at(
        self, test_session: AsyncSession, worker_user: User
    ) -> None:
        project = await _make_project(
            test_session, worker_user, status=ProjectStage.RESEARCHING.value
        )

        project.status = ProjectStage.WRITING.value
        await test_session.commit()
        await test_session.refresh(project)

        assert project.completed_at is None
