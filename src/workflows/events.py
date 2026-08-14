"""진행 이벤트 인프로세스 pub/sub 브로커 — 러너/스테이지/토큰트래커 → WebSocket.

러너는 요청-응답 밖 asyncio 백그라운드 태스크로 돌고, WS 엔드포인트도 같은 프로세스·
이벤트 루프에 산다. 그래서 프로세스 전역 싱글턴 브로커로 project_id별 fan-out이 성립한다.

⚠️ 단일 워커 전제. uvicorn을 --workers>1로 띄우면 러너 태스크와 WS 구독이 다른 프로세스에
갈릴 수 있어 이 인메모리 브로커로는 이어지지 않는다(그 경우 Redis pub/sub 등 외부 브로커 필요).

메시지 JSON은 프론트 계약(web/src/api/ws-messages.ts)의 discriminated union과 1:1:
phase / step / stream / fake_stream / cost / checkpoint / error 7종. 'done'은 별도 타입이 아니라
{type:phase, phase:export, status:completed}로 표현하고, cost는 누적값(delta 아님)을 보낸다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import structlog

from src.core.clock import now as clock_now

logger = structlog.get_logger(__name__)

_QUEUE_MAX = 512

# 프론트 enum과 일치해야 하는 값들(오타 방지용 상수)
PHASES = ("research", "indexing", "writing", "qa", "export")
STEP_STATUSES = ("started", "completed", "failed")
PHASE_STATUSES = ("started", "completed")

# 백엔드 게이트(ReviewGate) → 프론트 checkpoint.level(1|2). 그 외는 2로.
# level은 **순서가 아니라 거친 분류**다(프론트 zod가 1|2 리터럴 union이라 3은 파싱 실패).
# 1 = 작성 전 준비 단계 결정(설계·자료), 2 = 본문이 생긴 뒤의 결정. 그래서 design_brief를
# 앞에 넣어도 source_pool을 2로 밀 필요가 없다.
_GATE_LEVEL = {"design_brief": 1, "source_pool": 1, "qa_select": 2}


def gate_level(gate: str) -> int:
    return _GATE_LEVEL.get(gate, 2)


class ProgressBroker:
    """project_id별 구독 큐 fan-out. 발행은 비차단(느린 구독자는 이벤트를 잃을 뿐)."""

    def __init__(self) -> None:
        self._subs: dict[uuid.UUID, set[asyncio.Queue[dict]]] = {}

    def subscribe(self, project_id: uuid.UUID) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subs.setdefault(project_id, set()).add(queue)
        return queue

    def unsubscribe(self, project_id: uuid.UUID, queue: asyncio.Queue[dict]) -> None:
        subs = self._subs.get(project_id)
        if subs is None:
            return
        subs.discard(queue)
        if not subs:
            self._subs.pop(project_id, None)

    def has_subscribers(self, project_id: uuid.UUID) -> bool:
        return bool(self._subs.get(project_id))

    def publish(self, project_id: uuid.UUID, message: dict) -> None:
        """구독 큐 전체에 비차단 발행. 큐가 가득 차면 가장 오래된 것을 버리고 최신을 넣는다.

        발행자(러너/스테이지)를 절대 막지 않는다 — 구독 시 스냅샷을 먼저 보내므로
        일부 이벤트를 잃어도 상태 정합성은 폴링 재동기화로 회복된다."""
        for queue in list(self._subs.get(project_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


# 프로세스 전역 싱글턴
progress_broker = ProgressBroker()

# 프로젝트별 최근 세부 단계(단일 워커 전제) — WS 미구독 화면(개요 스테퍼의 HTTP 폴링)도
# 색인·RAPTOR 같은 수 분짜리 단계의 내부 진행을 보게 하는 용도. 진행 API가 읽어 노출한다.
_last_steps: dict[uuid.UUID, dict] = {}


def last_step(project_id: uuid.UUID) -> dict | None:
    """최근 emit_step 메시지({phase, step, status}) — 없으면 None."""
    return _last_steps.get(project_id)


# 프로젝트별 마지막 이벤트 시각(단일 워커 전제) — 죽은/고착된 런 판정용. 진행 API가
# 노출한다. 인메모리라 재시작에 사라지지만, 그때는 runner_alive=False가 먼저 잡는다.
_last_event_at: dict[uuid.UUID, datetime] = {}


def last_event_at(project_id: uuid.UUID) -> datetime | None:
    """이 프로젝트로 마지막 진행 이벤트를 발행한 시각 — 없으면 None."""
    return _last_event_at.get(project_id)


def _publish(project_id: uuid.UUID, message: dict) -> None:
    _last_event_at[project_id] = clock_now()
    progress_broker.publish(project_id, message)


# ── emit 헬퍼(no-op safe: 구독자가 없으면 그냥 버려진다) ─────────────────────


def emit_phase(project_id: uuid.UUID | None, phase: str, status: str) -> None:
    if project_id is None:
        return
    if status == "completed":
        # 단계 종료 시 세부 단계 잔재 정리 — 다음 단계의 첫 emit_step이 다시 채운다.
        _last_steps.pop(project_id, None)
    _publish(project_id, {"type": "phase", "phase": phase, "status": status})


def emit_step(
    project_id: uuid.UUID | None,
    phase: str,
    step: str,
    status: str,
    *,
    eta_seconds: int | None = None,
) -> None:
    if project_id is None:
        return
    msg: dict = {"type": "step", "phase": phase, "step": step, "status": status}
    if eta_seconds is not None:
        msg["eta_seconds"] = eta_seconds
    _last_steps[project_id] = msg
    _publish(project_id, msg)


def emit_cost(project_id: uuid.UUID | None, tokens_used: int, cost_usd: float) -> None:
    """누적 토큰·비용. 프론트는 이 값으로 교체 표시하므로 반드시 누적값을 보낸다."""
    if project_id is None:
        return
    _publish(
        project_id,
        {"type": "cost", "tokens_used": int(tokens_used), "cost_usd": round(float(cost_usd), 6)},
    )


def emit_checkpoint(project_id: uuid.UUID | None, checkpoint_id: str, level: int) -> None:
    if project_id is None:
        return
    _publish(
        project_id,
        {
            "type": "checkpoint",
            "checkpoint_id": checkpoint_id,
            "level": level,
            "requires_user_decision": True,
        },
    )


def emit_error(project_id: uuid.UUID | None, code: str, message: str) -> None:
    if project_id is None:
        return
    _publish(project_id, {"type": "error", "code": code, "message": message})
