"""진행 상황 WebSocket — 브로커(pub/sub)와 WS 핸들러의 인증·초기 스냅샷 검증.

TestClient(starlette)는 별도 스레드·이벤트 루프에서 앱을 돌려 asyncpg 엔진과
크로스-루프 충돌이 날 수 있으므로, WS 엔드포인트의 핵심 로직(_authorize·
_initial_snapshot)을 가짜 WebSocket으로 직접 호출해 같은 루프에서 검증한다.
브로드캐스트 동작은 ProgressBroker 단위 테스트가 커버한다.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routers.ws import _authorize, _initial_snapshot
from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage
from src.infrastructure.auth.jwt_handler import create_access_token
from src.workflows.events import (
    emit_checkpoint,
    emit_cost,
    emit_phase,
    emit_step,
    gate_level,
    progress_broker,
)


class _FakeWebSocket:
    """_authorize/_initial_snapshot가 쓰는 최소 인터페이스만 흉내낸다."""

    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self.cookies = cookies or {}
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class TestProgressBroker:
    def test_subscribe_publish_unsubscribe(self) -> None:
        pid = uuid.uuid4()
        q = progress_broker.subscribe(pid)
        assert progress_broker.has_subscribers(pid)
        progress_broker.publish(pid, {"type": "phase", "phase": "research", "status": "started"})
        assert q.get_nowait() == {"type": "phase", "phase": "research", "status": "started"}
        progress_broker.unsubscribe(pid, q)
        assert not progress_broker.has_subscribers(pid)
        progress_broker.publish(pid, {"type": "cost", "tokens_used": 1, "cost_usd": 0.1})

    def test_fanout_to_multiple_subscribers(self) -> None:
        pid = uuid.uuid4()
        a = progress_broker.subscribe(pid)
        b = progress_broker.subscribe(pid)
        emit_step(pid, "writing", "본문 작성 · 1.1 배경", "started", eta_seconds=30)
        for q in (a, b):
            assert q.get_nowait() == {
                "type": "step",
                "phase": "writing",
                "step": "본문 작성 · 1.1 배경",
                "status": "started",
                "eta_seconds": 30,
            }
        progress_broker.unsubscribe(pid, a)
        progress_broker.unsubscribe(pid, b)

    def test_emit_helpers_shape(self) -> None:
        pid = uuid.uuid4()
        q = progress_broker.subscribe(pid)
        emit_phase(pid, "writing", "started")
        emit_cost(pid, 1234, 0.5)
        emit_checkpoint(pid, "cp-1", 2)
        assert q.get_nowait() == {"type": "phase", "phase": "writing", "status": "started"}
        assert q.get_nowait() == {"type": "cost", "tokens_used": 1234, "cost_usd": 0.5}
        assert q.get_nowait() == {
            "type": "checkpoint",
            "checkpoint_id": "cp-1",
            "level": 2,
            "requires_user_decision": True,
        }
        progress_broker.unsubscribe(pid, q)

    def test_gate_level_mapping(self) -> None:
        assert gate_level("source_pool") == 1
        assert gate_level("qa_select") == 2
        assert gate_level("final") == 2  # 미지정은 2로


async def _make_project(session: AsyncSession, owner_id: uuid.UUID, status: str) -> Project:
    project = Project(
        title="WS 테스트",
        topic="실시간",
        preset=None,
        config={},
        depth_mode="standard",
        owner_id=owner_id,
        status=status,
    )
    session.add(project)
    await session.commit()
    return project


class TestProgressWsAuth:
    async def test_reject_without_cookie(
        self, test_session: AsyncSession, super_admin_user
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id, "researching")
        assert await _authorize(_FakeWebSocket(), project.id) is None

    async def test_accept_owner(self, test_session: AsyncSession, super_admin_user) -> None:
        project = await _make_project(test_session, super_admin_user.id, "researching")
        ws = _FakeWebSocket({"access_token": create_access_token(super_admin_user.id)})
        user = await _authorize(ws, project.id)
        assert user is not None and user.id == super_admin_user.id

    async def test_reject_other_worker(
        self, test_session: AsyncSession, super_admin_user, worker_user
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id, "researching")
        ws = _FakeWebSocket({"access_token": create_access_token(worker_user.id)})
        assert await _authorize(ws, project.id) is None

    async def test_initial_snapshot_cost_and_gate(
        self, test_session: AsyncSession, super_admin_user
    ) -> None:
        project = await _make_project(test_session, super_admin_user.id, "researching")
        # 토큰 사용 2건 심기 → 누적 합산 검증
        test_session.add_all(
            [
                TokenUsage(
                    project_id=project.id,
                    model="m",
                    operation="x",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.2,
                    mode="replay",
                ),
                TokenUsage(
                    project_id=project.id,
                    model="m",
                    operation="x",
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=0.05,
                    mode="replay",
                ),
            ]
        )
        await test_session.commit()

        ws = _FakeWebSocket()
        await _initial_snapshot(ws, project.id)
        assert ws.sent[0]["type"] == "cost"
        assert ws.sent[0]["tokens_used"] == 165  # 100+50+10+5
        assert abs(ws.sent[0]["cost_usd"] - 0.25) < 1e-6
        # pending 게이트 없으면 checkpoint 미전송
        assert all(m["type"] != "checkpoint" for m in ws.sent)
