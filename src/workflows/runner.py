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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import app_settings
from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import ProjectStage, ReviewGate
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.review_point import ReviewPoint
from src.db.models.user import User
from src.db.session import async_session_maker
from src.infrastructure.naver_works.bot import send_bot_message
from src.workflows.events import emit_checkpoint, emit_error, gate_level
from src.workflows.pipeline import Paused, advance
from src.workflows.write_loop import apply_selection, plan_from_payload, rehydrate_from_payload

logger = structlog.get_logger(__name__)

# GC 방지를 위해 살아있는 백그라운드 태스크 참조를 유지한다.
_TASKS: set[asyncio.Task[Any]] = set()


async def _notify_safe(
    owner_id: uuid.UUID, project_id: uuid.UUID, result_type: str, page: str
) -> None:
    """소유자에게 네이버웍스 봇 알림 — 실패는 로깅만, 파이프라인을 절대 막지 않는다.

    result_type은 봇 템플릿 키(success=완료, partial=검토 대기, failed=실패).
    """
    if not app_settings.get_bool("notify_enabled"):
        return
    try:
        async with async_session_maker() as session:
            owner = await session.get(User, owner_id)
        if owner is None or not owner.is_active:
            return
        await send_bot_message(
            target_email=owner.email,
            user_name=owner.name,
            result_url=f"{settings.react_frontend_url}/projects/{project_id}/{page}",
            result_type=result_type,
        )
    except Exception:
        logger.warning("project.notify_failed", project_id=str(project_id), exc_info=True)


def _parse_uuid_list(raw: Any) -> list[uuid.UUID]:
    """결정 payload의 UUID 문자열 목록을 안전하게 파싱(잘못된 값은 무시)."""
    if not isinstance(raw, list):
        return []
    out: list[uuid.UUID] = []
    for x in raw:
        try:
            out.append(uuid.UUID(str(x)))
        except (ValueError, TypeError):
            continue
    return out


async def _apply_source_pool_exclusions(
    session: AsyncSession, project_id: uuid.UUID, decision: dict[str, Any]
) -> int:
    """SOURCE_POOL 결정의 excluded_source_ids를 project_sources.is_included=false로 반영.

    resume_run의 세션에서 gate resolve와 같은 커밋으로 처리한다 — 작성 단계 검색이
    별도 세션에서 is_included를 읽으므로, advance(=write) 전에 커밋돼 있어야 제외가 실효된다.
    반환값은 제외된 출처 수.
    """
    excluded = _parse_uuid_list(decision.get("excluded_source_ids"))
    if not excluded:
        return 0
    await session.execute(
        update(ProjectSource)
        .where(ProjectSource.project_id == project_id, ProjectSource.id.in_(excluded))
        .values(is_included=False)
    )
    return len(excluded)


def _state_from_project(project: Project) -> ProjectState:
    return ProjectState.from_db(
        {
            "id": project.id,
            "owner_id": project.owner_id,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "topic": project.topic,
            "preset": project.preset,
            "depth_mode": project.depth_mode,
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
    owner_id: uuid.UUID | None = None
    entered_from_created = False
    try:
        async with async_session_maker() as session:
            project = await session.get(Project, project_id)
            if project is None:
                logger.warning("project.missing", project_id=str(project_id))
                return
            owner_id = project.owner_id

            state = _state_from_project(project)
            if project.status == ProjectStage.CREATED.value:
                # 표시용 선행 전이: 실행에 들어간 순간부터 UI가 '시작 전'으로
                # 보이지 않게 researching으로 먼저 영속화한다. 척추 진행 판단은
                # 위에서 이미 만든 in-memory state(CREATED) 기준이라 단계를
                # 건너뛰지 않는다(엔드포인트 상태 선점 사고와 다른 지점).
                entered_from_created = True
                project.status = ProjectStage.RESEARCHING.value
                await session.commit()
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
                # 실시간: 검토 게이트 도달을 WS로 즉시 알림(프론트가 결정 UI 노출)
                emit_checkpoint(
                    project_id,
                    str(outcome.review.id),
                    gate_level(outcome.review.gate.value),
                )
                # 검토 차례 알림 (partial=확인 필요 템플릿)
                await _notify_safe(owner_id, project_id, "partial", page="progress")
            else:
                await session.commit()
                logger.info("project.completed", project_id=str(project_id))
                await _notify_safe(owner_id, project_id, "success", page="export")
    except Exception:
        logger.exception("project.run_failed", project_id=str(project_id))
        if entered_from_created:
            # 첫 구간(research)에서 죽은 실행은 created로 복귀 — 사용자가
            # '작성 시작'으로 재시도할 수 있어야 한다(researching 고착 방지).
            try:
                async with async_session_maker() as recovery:
                    await recovery.execute(
                        update(Project)
                        .where(
                            Project.id == project_id,
                            Project.status == ProjectStage.RESEARCHING.value,
                        )
                        .values(status=ProjectStage.CREATED.value)
                    )
                    await recovery.commit()
            except Exception:
                logger.warning("project.status_rollback_failed", project_id=str(project_id))
        emit_error(project_id, "run_failed", "실행 중 오류가 발생했습니다")
        if owner_id is not None:
            await _notify_safe(owner_id, project_id, "failed", page="progress")


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


# 프로젝트별 실행 중복 방지 — 단일 워커(인프로세스 이벤트 루프) 전제.
# 연타·중복 요청이 와도 프로젝트당 파이프라인 태스크는 하나만 산다.
# (상태 선점 방식은 금지: status는 척추의 진행 위치라 미리 바꾸면 단계를 건너뛴다)
_RUNNING: set[uuid.UUID] = set()


def _spawn_guarded(project_id: uuid.UUID) -> bool:
    """이미 실행 중이면 False. 아니면 _execute를 spawn하고 True."""
    if project_id in _RUNNING:
        logger.warning("project.already_running", project_id=str(project_id))
        return False
    _RUNNING.add(project_id)

    async def _run() -> None:
        try:
            await _execute(project_id)
        finally:
            _RUNNING.discard(project_id)

    _spawn(_run())
    return True


async def start_run(project_id: uuid.UUID) -> bool:
    """프로젝트 실행 시작(백그라운드). 이미 실행 중이면 False, 시작했으면 True."""
    return _spawn_guarded(project_id)


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
        # 자료 풀 확정: 사람이 제외한 출처를 같은 커밋에서 is_included=false로 반영한다.
        if review.gate == ReviewGate.SOURCE_POOL.value:
            n_excluded = await _apply_source_pool_exclusions(session, project_id, decision)
            if n_excluded:
                logger.info("source_pool.pruned", project_id=str(project_id), excluded=n_excluded)
        await session.commit()
    _spawn_guarded(project_id)
