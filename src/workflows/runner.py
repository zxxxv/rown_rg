"""프로젝트 실행 러너 — in-process asyncio 백그라운드 (I/O 계층).

요청-응답 밖에서 척추(pipeline.advance)를 돌린다(엔드포인트는 즉시 반환). 실행은
사용자 인증 세션을 들고 다니지 않는다: 귀속은 owner_id(데이터)로만. 따라서 사용자가
로그아웃/세션 무효화해도 실행은 영향받지 않는다.

상태 단일 진실 = 정규 DB 테이블. ProjectState는 from_db로 복원하는 인메모리 작업사본.
게이트는 review_points 행으로 영속화 → 프로세스 재시작에도 어느 게이트에서 멈췄는지
복구 가능(체크포인터 blob 없이).

"검토 대기"는 projects.status 값이 아니라 pending review_point의 존재로 표현한다.
status는 척추 위치(work 단계: created→researching→…→completed)를 담는다.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import now as clock_now
from src.core.state import ProjectState
from src.core.types import ProjectStage, ReviewGate
from src.db.models.project import Project
from src.db.models.review_point import ReviewPoint
from src.db.session import async_session_maker
from src.workflows.pipeline import Paused, advance
from src.workflows.write_loop import apply_selection, plan_from_payload, rehydrate_from_payload

logger = structlog.get_logger(__name__)

# GC 방지를 위해 살아있는 백그라운드 태스크 참조를 유지한다.
_TASKS: set[asyncio.Task[Any]] = set()


def _state_from_project(project: Project) -> ProjectState:
    return ProjectState.from_db(
        {
            "id": project.id,
            "owner_id": project.owner_id,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "topic": project.topic,
            "preset": project.preset,
            "status": project.status,
            "config": project.config,
        }
    )


async def _rehydrate_section_plan(
    session: AsyncSession, project_id: uuid.UUID, state: ProjectState
) -> ProjectState:
    """resume 시 SOURCE_POOL review payload에서 section_plan을 되살린다.

    plan은 projects 테이블에 없어 게이트 payload가 유일한 복원원이다. RESEARCHING
    (→write 재개)이 아니거나 plan이 이미 있으면 그대로 둔다(멱등).
    """
    if state.current_stage is not ProjectStage.RESEARCHING or state.section_plan:
        return state
    review = (
        await session.execute(
            select(ReviewPoint)
            .where(
                ReviewPoint.project_id == project_id,
                ReviewPoint.gate == ReviewGate.SOURCE_POOL.value,
                ReviewPoint.status == "resolved",
            )
            .order_by(ReviewPoint.resolved_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if review is None:
        return state
    return state.with_section_plan(plan_from_payload(review.payload))


async def _rehydrate_qa_selection(
    session: AsyncSession, project_id: uuid.UUID, state: ProjectState
) -> ProjectState:
    """resume 시 QA_SELECT review의 payload(후보·plan)+decision(선택)을 state에 되살린다.

    section_plan·section_candidates는 projects 테이블에 없어 재구성 못 하므로, 게이트가
    payload에 실어둔 값에서 복원한다. REVIEWING 단계가 아니거나 해결된 QA_SELECT review가
    없으면 그대로 둔다(멱등).
    """
    if state.current_stage is not ProjectStage.REVIEWING:
        return state
    review = (
        await session.execute(
            select(ReviewPoint)
            .where(
                ReviewPoint.project_id == project_id,
                ReviewPoint.gate == ReviewGate.QA_SELECT.value,
                ReviewPoint.status == "resolved",
            )
            .order_by(ReviewPoint.resolved_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if review is None:
        return state
    state = rehydrate_from_payload(state, review.payload)
    selections = (review.decision or {}).get("selections", {})
    if selections:
        state = apply_selection(state, selections)
    return state


async def _execute(project_id: uuid.UUID) -> None:
    """척추를 현재 단계부터 게이트 또는 완료까지 한 구간 전진시키고 영속화한다."""
    try:
        async with async_session_maker() as session:
            project = await session.get(Project, project_id)
            if project is None:
                logger.warning("project.missing", project_id=str(project_id))
                return

            state = _state_from_project(project)
            state = await _rehydrate_section_plan(session, project.id, state)
            state = await _rehydrate_qa_selection(session, project.id, state)
            outcome = await advance(state)
            project.status = outcome.state.current_stage.value

            if isinstance(outcome, Paused):
                # 게이트 대기 진입 — pending review_point 영속화(재시작 복구·감사 이력)
                session.add(
                    ReviewPoint(
                        id=outcome.review.id,
                        project_id=project.id,
                        gate=outcome.review.gate.value,
                        payload=outcome.review.payload,
                        status="pending",
                        created_at=outcome.review.created_at,
                    )
                )
                await session.commit()
                logger.info(
                    "project.gate", project_id=str(project_id), gate=outcome.review.gate.value
                )
            else:
                await session.commit()
                logger.info("project.completed", project_id=str(project_id))
    except Exception:
        logger.exception("project.run_failed", project_id=str(project_id))


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def get_pending_gate(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any] | None:
    """해당 프로젝트가 대기 중인 게이트(없으면 None). progress/decide가 사용."""
    review = (
        await session.execute(
            select(ReviewPoint)
            .where(ReviewPoint.project_id == project_id, ReviewPoint.status == "pending")
            .order_by(ReviewPoint.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if review is None:
        return None
    return {"review_point_id": str(review.id), "gate": review.gate, "payload": review.payload}


async def start_run(project_id: uuid.UUID) -> None:
    """프로젝트 실행 시작(백그라운드). 즉시 반환된다."""
    _spawn(_execute(project_id))


async def resume_run(project_id: uuid.UUID, decision: dict[str, Any]) -> None:
    """대기 중인 게이트를 사용자 결정으로 resolved 처리하고 재개(백그라운드)."""
    async with async_session_maker() as session:
        review = (
            await session.execute(
                select(ReviewPoint)
                .where(ReviewPoint.project_id == project_id, ReviewPoint.status == "pending")
                .order_by(ReviewPoint.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if review is None:
            logger.warning("project.no_pending_gate", project_id=str(project_id))
            return
        review.status = "resolved"
        review.decision = decision
        review.resolved_at = clock_now()
        await session.commit()
    _spawn(_execute(project_id))
