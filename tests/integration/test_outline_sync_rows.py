"""sync_rows_to_plan — 목차 변경 후 절 행 재정렬(절 id 기준, 2026-08-21 수술).

계약: 같은 id의 행은 본문·근거·상태를 지키고 번호·제목만 새 목차를 따른다.
plan에서 빠진 행은 지워지고, 새 절은 빈 pending 행으로 생긴다. 자리 맞바꿈이
uq_section_pos에 걸리지 않는다(전량 삭제 후 재삽입 패턴).
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.types import SectionPlan
from src.db.models.project import Project
from src.db.models.section import Section
from src.db.models.user import User
from src.infrastructure.auth import password_handler
from src.services.sections.store import sync_rows_to_plan


async def _seed_project(session: AsyncSession) -> Project:
    user = User(
        email=f"sync-{uuid4().hex[:8]}@example.com",
        name="동기화 테스트",
        role="worker",
        is_active=True,
        password_hash=password_handler.hash_password("Smoke-2026!!aa"),
    )
    session.add(user)
    await session.flush()
    project = Project(
        title="절 재정렬",
        topic="주제",
        config={},
        owner_id=user.id,
        status="reviewing",
    )
    session.add(project)
    await session.flush()
    return project


def _plan(sid, ch: int, sec: int, title: str, chapter_title: str = "장") -> SectionPlan:
    return SectionPlan(
        section_id=sid,
        chapter_number=ch,
        section_number=sec,
        title=title,
        chapter_title=chapter_title,
    )


def _row(project_id, sid, ch: int, sec: int, title: str, content: str, meta: dict | None = None):
    return Section(
        id=sid,
        project_id=project_id,
        chapter_number=ch,
        section_number=sec,
        chapter_title="옛 장",
        title=title,
        level=2,
        content=content,
        source_ids=[],
        meta=meta or {},
        qa_status="passed",
        status="completed",
    )


class TestSyncRowsToPlan:
    async def test_swap_renumber_keeps_content_and_survives_unique(
        self, test_session: AsyncSession
    ):
        project = await _seed_project(test_session)
        a, b = uuid4(), uuid4()
        test_session.add_all(
            [
                _row(project.id, a, 1, 1, "가", "가 본문"),
                _row(project.id, b, 1, 2, "나", "나 본문"),
            ]
        )
        await test_session.flush()
        # 자리 맞바꿈 — 행 단위 UPDATE라면 uq_section_pos에 걸리는 경우.
        await sync_rows_to_plan(
            test_session,
            project.id,
            [_plan(b, 1, 1, "나(개정)", "새 장"), _plan(a, 1, 2, "가", "새 장")],
        )
        rows = (
            (
                await test_session.execute(
                    select(Section)
                    .where(Section.project_id == project.id)
                    .order_by(Section.chapter_number, Section.section_number)
                )
            )
            .scalars()
            .all()
        )
        assert [(r.id, r.title, r.content) for r in rows] == [
            (b, "나(개정)", "나 본문"),
            (a, "가", "가 본문"),
        ]
        assert {r.chapter_title for r in rows} == {"새 장"}
        assert all(r.status == "completed" for r in rows)

    async def test_removed_rows_dropped_and_new_sections_pending(self, test_session: AsyncSession):
        project = await _seed_project(test_session)
        keep, drop = uuid4(), uuid4()
        test_session.add_all(
            [
                _row(project.id, keep, 1, 1, "가", "가 본문"),
                _row(project.id, drop, 1, 2, "나", "나 본문"),
            ]
        )
        await test_session.flush()
        fresh = uuid4()
        await sync_rows_to_plan(
            test_session,
            project.id,
            [_plan(keep, 1, 1, "가"), _plan(fresh, 2, 1, "새 절", "2장")],
        )
        rows = {
            r.id: r
            for r in (
                await test_session.execute(select(Section).where(Section.project_id == project.id))
            )
            .scalars()
            .all()
        }
        assert set(rows) == {keep, fresh}
        assert rows[keep].content == "가 본문"
        assert rows[fresh].status == "pending"
        assert rows[fresh].content == ""

    async def test_ledger_and_chain_labels_follow_new_number(self, test_session: AsyncSession):
        project = await _seed_project(test_session)
        sid = uuid4()
        meta = {
            "ledger_entries": [{"section_ref": "1.1", "metric": "총사업비", "value": "100억"}],
            "chain_summary": {"section": "1.1", "summary": "요약"},
        }
        test_session.add(_row(project.id, sid, 1, 1, "가", "본문", meta))
        await test_session.flush()
        await sync_rows_to_plan(test_session, project.id, [_plan(sid, 3, 2, "가")])
        row = (await test_session.execute(select(Section).where(Section.id == sid))).scalar_one()
        assert row.meta["ledger_entries"][0]["section_ref"] == "3.2"
        assert row.meta["ledger_entries"][0]["value"] == "100억"
        assert row.meta["chain_summary"]["section"] == "3.2"

    async def test_no_rows_is_noop(self, test_session: AsyncSession):
        project = await _seed_project(test_session)
        await sync_rows_to_plan(test_session, project.id, [_plan(uuid4(), 1, 1, "가")])
        n = (
            await test_session.execute(select(Section).where(Section.project_id == project.id))
        ).all()
        assert n == []  # 작성 전에는 행을 만들지 않는다 — write가 처음부터 만든다
