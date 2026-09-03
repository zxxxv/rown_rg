"""진행 상황 WebSocket — 러너 이벤트 실시간 스트리밍.

경로 `/ws/projects/{id}/progress` (앱 루트, /api/v1 아님 — 프론트 same-origin 계약).
서버→클라 단방향 push. 클라이언트는 아무것도 보내지 않는다(순수 구독).
메시지 형태는 events.py(=web/src/api/ws-messages.ts) 계약과 1:1.

인증: same-origin 쿠키의 access_token을 검증하고 프로젝트 소유/관리자 권한을 확인한다.
완료 후에도 소켓을 닫지 않는다(닫으면 프론트가 재연결 루프에 빠짐).
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from src.api.dependencies.permissions import ADMINS
from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from src.db.session import open_session
from src.infrastructure.auth.jwt_handler import decode_token
from src.workflows.events import gate_level, progress_broker
from src.workflows.runner import get_pending_gate

logger = structlog.get_logger(__name__)

router = APIRouter()


async def _authorize(websocket: WebSocket, project_id: uuid.UUID) -> User | None:
    """쿠키 access_token 검증 + 프로젝트 접근 권한. 실패 시 None."""
    token = websocket.cookies.get("access_token")
    if not token:
        return None
    try:
        token_data = decode_token(token)
    except Exception:
        return None
    if token_data.token_type != "access":
        return None
    async with open_session() as session:
        user = await session.get(User, token_data.user_id)
        if user is None or not user.is_active:
            return None
        project = await session.get(Project, project_id)
        if project is None:
            return None
        if project.owner_id != user.id and user.role not in ADMINS:
            return None
    return user


async def _initial_snapshot(websocket: WebSocket, project_id: uuid.UUID) -> None:
    """접속 즉시 현재 상태를 동기화 — 누적 비용(DB 합산) + 대기 게이트."""
    async with open_session() as session:
        totals = (
            await session.execute(
                select(
                    func.coalesce(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0),
                    func.coalesce(func.sum(TokenUsage.cost_usd), 0),
                ).where(TokenUsage.project_id == project_id)
            )
        ).one()
        gate = await get_pending_gate(session, project_id)
    await websocket.send_json(
        {"type": "cost", "tokens_used": int(totals[0]), "cost_usd": round(float(totals[1]), 6)}
    )
    if gate is not None:
        await websocket.send_json(
            {
                "type": "checkpoint",
                "checkpoint_id": gate["review_point_id"],
                "level": gate_level(gate["gate"]),
                "requires_user_decision": True,
            }
        )


@router.websocket("/ws/projects/{project_id}/progress")
async def progress_ws(websocket: WebSocket, project_id: uuid.UUID) -> None:
    user = await _authorize(websocket, project_id)
    if user is None:
        # 1008 = policy violation (인증/권한 실패)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = progress_broker.subscribe(project_id)
    try:
        await _initial_snapshot(websocket, project_id)

        async def _sender() -> None:
            while True:
                message = await queue.get()
                await websocket.send_json(message)

        async def _receiver() -> None:
            # 클라이언트는 아무것도 보내지 않는다 — 끊기면 예외가 나 루프가 끝난다.
            while True:
                await websocket.receive_text()

        sender = asyncio.create_task(_sender())
        receiver = asyncio.create_task(_receiver())
        _, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("ws.progress_error", project_id=str(project_id), exc_info=True)
    finally:
        progress_broker.unsubscribe(project_id, queue)
