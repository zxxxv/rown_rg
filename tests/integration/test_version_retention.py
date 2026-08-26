"""버전 보존 — 잦은 수정은 최근 것만, 이정표는 전부(2026-08-27).

스냅샷 한 벌이 **보고서 전체**다. 실측 최대 415 kB(예타 35절)라, 블록 수정 100번이면
그 프로젝트 하나가 41 MB가 된다. 보존 정책이 아예 없어 무한히 자라고 있었다.

되돌릴 대상은 거의 언제나 직전 몇 개이고, 더 옛것으로 가려는 사람은 이정표
(조립·확정·다시열기·목차·자료·수동·원본)를 짚는다. 그래서 그 둘을 갈랐다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.report_version import ReportVersion
from src.db.models.section import Section
from src.db.models.user import User
from src.services.sections.versions import KEEP_RECENT_CHURN, prune_versions, snapshot_report
from tests.integration.test_drift_api import _completed_project


async def _reasons(session: AsyncSession, pid: uuid.UUID) -> list[str]:
    rows = (
        await session.execute(
            select(ReportVersion.reason)
            .where(ReportVersion.project_id == pid)
            .order_by(ReportVersion.version_no)
        )
    ).scalars()
    return list(rows)


class TestVersionRetention:
    async def test_keeps_milestones_and_recent_edits(
        self, worker_user: User, test_session: AsyncSession
    ):
        """이정표는 전부 남고, 잦은 수정은 최근 것만 남는다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        row = await test_session.get(Section, sid)

        await snapshot_report(test_session, pid, reason="assemble")
        # 본문을 조금씩 바꿔 가며 잦은 수정을 상한보다 많이 쌓는다.
        # (내용 지문이 같으면 새 벌을 안 만들므로 매번 달라야 한다)
        for i in range(KEEP_RECENT_CHURN + 5):
            row.content = f"{i}번째 손질입니다."
            await test_session.flush()
            await snapshot_report(test_session, pid, reason=f"edit:1.{i}")
        # 내용 지문이 같으면 새 벌을 안 만드는 게 기존 계약이라, 확정 전에 한 번 더 바꾼다.
        row.content = "확정본입니다."
        await test_session.flush()
        await snapshot_report(test_session, pid, reason="finalize")
        await test_session.commit()

        reasons = await _reasons(test_session, pid)
        churn = [r for r in reasons if r.startswith("edit:")]
        milestones = [r for r in reasons if not r.startswith("edit:")]

        assert milestones == ["assemble", "finalize"], f"이정표가 사라졌다: {milestones}"
        assert len(churn) == KEEP_RECENT_CHURN, f"잦은 수정이 {len(churn)}개 남았다"
        # 남은 것은 **최근** 것이어야 한다 - 되돌릴 대상은 직전 몇 개다.
        assert churn[-1] == f"edit:1.{KEEP_RECENT_CHURN + 4}"
        assert "edit:1.0" not in churn

    async def test_prune_is_a_no_op_below_the_limit(
        self, worker_user: User, test_session: AsyncSession
    ):
        """상한 아래면 아무것도 안 지운다 - 평범한 편집은 전부 남는다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        row = await test_session.get(Section, sid)
        for i in range(3):
            row.content = f"{i}번째 손질입니다."
            await test_session.flush()
            await snapshot_report(test_session, pid, reason=f"edit:1.{i}")
        await test_session.commit()

        assert await prune_versions(test_session, pid) == 0
        assert len(await _reasons(test_session, pid)) == 3

    async def test_milestones_alone_are_never_pruned(
        self, worker_user: User, test_session: AsyncSession
    ):
        """이정표만 많아도 지우지 않는다 - 문서의 마디는 개수로 자르지 않는다."""
        sid = uuid.uuid4()
        pid = await _completed_project(test_session, worker_user.id, sid, "원래 방향")
        row = await test_session.get(Section, sid)
        for i in range(KEEP_RECENT_CHURN + 5):
            row.content = f"{i}번째 판입니다."
            await test_session.flush()
            await snapshot_report(test_session, pid, reason="manual")
        await test_session.commit()

        reasons = await _reasons(test_session, pid)
        assert len(reasons) == KEEP_RECENT_CHURN + 5
        assert set(reasons) == {"manual"}
