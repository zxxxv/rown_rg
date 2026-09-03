"""/run 게이트 우회 가드 통합 테스트.

게이트 대기(pending review_point)는 status가 작업 단계에 머물고 러너 태스크도 없어,
크래시 복구용 재개 조건과 겉모습이 같다. 가드 전에는 /run이 승인 없이 다음 단계로
넘어갔다(자료 미확정 작성 + section_plan 미복원 → write.plan_fallback 목차 붕괴).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import now as clock_now
from src.db.models.project import Project
from src.db.models.review_point import ReviewPoint
from src.db.models.user import User
from tests.conftest import auth_headers as _auth

pytestmark = pytest.mark.integration


async def _insert_project_with_gate(
    session: AsyncSession, owner_id: uuid.UUID, *, gate_status: str
) -> uuid.UUID:
    proj = Project(
        title="게이트 가드 테스트",
        topic="테스트 주제",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status="researching",  # SOURCE_POOL 대기 중의 실제 척추 위치
    )
    session.add(proj)
    await session.flush()
    session.add(
        ReviewPoint(
            project_id=proj.id,
            gate="source_pool",
            payload={},
            status=gate_status,
            resolved_at=clock_now() if gate_status == "resolved" else None,
        )
    )
    await session.commit()
    return proj.id


class TestRunGateGuard:
    async def test_run_blocked_while_gate_pending(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
    ) -> None:
        pid = await _insert_project_with_gate(test_session, worker_user.id, gate_status="pending")

        resp = await test_client.post(f"/api/v1/projects/{pid}/run", headers=_auth(worker_token))

        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "GATE_PENDING"
        # 상태가 그대로여야 한다 — 재개가 시작되지 않았다는 관측 가능한 증거.
        proj = await test_session.get(Project, pid)
        assert proj is not None
        await test_session.refresh(proj)
        assert proj.status == "researching"

    async def test_run_allowed_after_gate_resolved(
        self,
        test_client: AsyncClient,
        worker_token: str,
        worker_user: User,
        test_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 가드가 pending에만 반응해야 한다 — resolved 이력이 크래시 복구 재개를 막으면 안 된다.
        pid = await _insert_project_with_gate(test_session, worker_user.id, gate_status="resolved")

        async def _stub_start_run(_: uuid.UUID) -> bool:
            return True  # 실제 파이프라인 기동 없이 수락 경로만 검증

        monkeypatch.setattr("src.api.routers.projects.start_run", _stub_start_run)

        resp = await test_client.post(f"/api/v1/projects/{pid}/run", headers=_auth(worker_token))

        assert resp.status_code == 202, resp.text
