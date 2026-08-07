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

from src.core.clock import now as clock_now
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import ProjectStage, ReviewGate, SourceRef, SourceType
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.review_point import ReviewPoint
from src.db.models.section import Section
from src.db.models.user import User
from src.db.session import async_session_maker
from src.infrastructure.naver_works.bot import send_bot_message
from src.workflows import cancel
from src.workflows.events import emit_checkpoint, emit_error, gate_level
from src.workflows.pipeline import Paused, advance
from src.workflows.write_loop import (
    apply_selection,
    overlay_working_copy,
    plan_from_payload,
    rehydrate_from_payload,
)

logger = structlog.get_logger(__name__)

# GC 방지를 위해 살아있는 백그라운드 태스크 참조를 유지한다.
_TASKS: set[asyncio.Task[Any]] = set()


async def _notify_safe(
    owner_id: uuid.UUID, project_id: uuid.UUID, result_type: str, page: str
) -> None:
    """소유자에게 네이버웍스 봇 알림 — 실패는 로깅만, 파이프라인을 절대 막지 않는다.

    프로젝트별 옵트인: config.notification_channels에 'naver_works'가 있을 때만 발송한다
    (전역 기본은 off — 사용자가 프로젝트에서 알림을 켜야 온다). result_type은 봇 템플릿
    키(success=완료, partial=검토 대기, failed=실패).
    """
    try:
        async with async_session_maker() as session:
            owner = await session.get(User, owner_id)
            project = await session.get(Project, project_id)
        if owner is None or not owner.is_active or project is None:
            return
        channels = (project.config or {}).get("notification_channels") or []
        if "naver_works" not in channels:
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

    plan은 projects 테이블에 없어 게이트 payload가 유일한 복원원이다. 확정 게이트 이후
    구간(RESEARCHING→index, INDEXING→write)에서 재개될 때 복원하며, 이미 plan이 있거나
    해당 구간이 아니면 그대로 둔다(멱등).
    """
    if (
        state.current_stage not in (ProjectStage.RESEARCHING, ProjectStage.INDEXING)
        or state.section_plan
    ):
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
    # 검토 중 사람이 고친 내용(sections 행 = 작업 사본)이 payload 후보보다 우선 —
    # 통합 검토 화면의 직접 편집·AI 재작성이 조립에 반영되는 유일한 경로.
    rows = (
        (await session.execute(select(Section).where(Section.project_id == project_id)))
        .scalars()
        .all()
    )
    working = {row.id: (row.content, [uuid.UUID(str(s)) for s in row.source_ids]) for row in rows}
    return overlay_working_copy(state, working)


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

            async def _persist_running_stage(stage: ProjectStage) -> None:
                """표시용 중간 전이 — 스테퍼가 색인·작성 진행을 실시간 따라오게 한다.

                척추 판단은 이미 만든 in-memory state 기준이라 선점이 아니다.
                실패는 로그만 남긴다(표시가 실행을 죽이면 안 된다).
                """
                try:
                    async with async_session_maker() as display_session:
                        await display_session.execute(
                            update(Project)
                            .where(Project.id == project_id)
                            .values(status=stage.value)
                        )
                        await display_session.commit()
                except Exception:
                    logger.warning("project.stage_display_failed", project_id=str(project_id))

            state = await _rehydrate_section_plan(session, project.id, state)
            state = await _rehydrate_qa_selection(session, project.id, state)
            if entered_from_created and not state.sources:
                # 부분 실패 후 재시작: 이전 실행이 스테이징해 둔 출처를 상태로 복원 —
                # collect가 기존 출처를 제외(중복 스테이징 방지)하고 모자란 만큼만 보충한다.
                rows = (
                    (
                        await session.execute(
                            select(ProjectSource).where(ProjectSource.project_id == project.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if rows:
                    state = state.add_sources([_source_ref_from_row(r) for r in rows])
            outcome = await advance(state, on_stage=_persist_running_stage)
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
    except cancel.RunCancelled:
        # 사용자 취소 — 실패가 아니라 깨끗한 중단으로 CANCELLED 확정(created 복귀 로직 회피).
        logger.info("project.cancelled", project_id=str(project_id))
        try:
            async with async_session_maker() as recovery:
                await recovery.execute(
                    update(Project)
                    .where(Project.id == project_id)
                    .values(status=ProjectStage.CANCELLED.value)
                )
                await recovery.execute(
                    update(ReviewPoint)
                    .where(ReviewPoint.project_id == project_id, ReviewPoint.status == "pending")
                    .values(
                        status="resolved",
                        resolved_at=clock_now(),
                        decision={"outcome": "cancelled"},
                    )
                )
                await recovery.commit()
        except Exception:
            logger.warning("project.cancel_persist_failed", project_id=str(project_id))
        emit_error(project_id, "cancelled", "실행을 취소했습니다")
    except Exception as exc:
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
        # 한도 초과는 원인이 명확한 실패 — 일반 오류로 뭉개지 말고 그대로 알린다
        # (진행 중 도달 케이스: 사전 검사는 통과했지만 실행 도중 한도에 닿음).
        from src.core.exceptions import QuotaExceededError

        if isinstance(exc, QuotaExceededError):
            emit_error(
                project_id, "quota_exceeded", f"{exc.message} - 한도 상향 후 다시 시작하세요"
            )
        else:
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


_RUNNING: set[uuid.UUID] = set()

# ── 전역 동시 실행 상한 (2026-08-05) ─────────────────────────────────────────
# 무거운 실행(풀런·게이트 재개·추가 수집)은 슬롯을 얻어야 시작한다 — 초과분은
# FIFO 대기(asyncio.Semaphore 대기 큐). 색인(임베딩 ONNX)이 CPU·메모리를 지배하므로
# 운영 스펙(2 vCPU/8GB) 기본값은 1, env MAX_CONCURRENT_RUNS로 조정.
# 대기열은 인프로세스라 재시작 시 사라진다(_RUNNING과 같은 단일 워커 전제).
_run_slots = asyncio.Semaphore(max(1, settings.max_concurrent_runs))
_WAITING: list[uuid.UUID] = []


def is_running(project_id: uuid.UUID) -> bool:
    """해당 프로젝트의 파이프라인 태스크가 이 프로세스에서 실행·대기 중인지 (단일 워커 전제)."""
    return project_id in _RUNNING


def queue_status(project_id: uuid.UUID) -> dict[str, int] | None:
    """실행 대기열에 있으면 {position(1부터), waiting_total}, 실행 중/무관이면 None."""
    try:
        idx = _WAITING.index(project_id)
    except ValueError:
        return None
    return {"position": idx + 1, "waiting_total": len(_WAITING)}


def _spawn_limited(project_id: uuid.UUID, work: Any, *, clear_cancel_on_exit: bool) -> bool:
    """중복 가드 + 전역 슬롯 하에 work 코루틴 팩토리를 spawn. 이미 실행/대기 중이면 False.

    슬롯이 없으면 FIFO로 대기하고, 대기 중 취소가 요청되면 실행 없이 빠진다.
    clear_cancel_on_exit: 풀런은 종료 시 취소 플래그를 정리하지만(다음 실행 보호),
    보충 수집은 기존 의미(플래그 유지 — 다음 재개에서 관측)를 보존한다.
    """
    if project_id in _RUNNING:
        logger.warning("project.already_running", project_id=str(project_id))
        return False
    _RUNNING.add(project_id)

    async def _run() -> None:
        _WAITING.append(project_id)
        try:
            async with _run_slots:
                _WAITING.remove(project_id)
                if cancel.is_requested(project_id):
                    # 대기 중 취소 — 슬롯만 반납하고 실행하지 않는다.
                    logger.info("run.cancelled_while_queued", project_id=str(project_id))
                    return
                await work()
        finally:
            if project_id in _WAITING:  # 대기 중 태스크가 죽은 비정상 경로 정리
                _WAITING.remove(project_id)
            _RUNNING.discard(project_id)
            if clear_cancel_on_exit:
                cancel.clear(project_id)  # 취소 요청이 남아도 다음 실행이 즉시 취소되지 않게

    _spawn(_run())
    return True


def _spawn_guarded(project_id: uuid.UUID) -> bool:
    """이미 실행 중이면 False. 아니면 _execute를 전역 슬롯 하에 spawn하고 True."""
    return _spawn_limited(project_id, lambda: _execute(project_id), clear_cancel_on_exit=True)


async def start_run(project_id: uuid.UUID) -> bool:
    """프로젝트 실행 시작(백그라운드). 이미 실행 중이면 False, 시작했으면 True."""
    return _spawn_guarded(project_id)


async def resume_run(project_id: uuid.UUID, decision: dict[str, Any]) -> None:
    """대기 중인 게이트를 사용자 결정으로 resolved 처리하고 재개(백그라운드).

    SOURCE_POOL에서 action=collect_more면 다음 단계로 가지 않고 보충 수집
    라운드 1회를 돌린 뒤 게이트를 다시 연다 — 사람이 누를 때마다 1라운드
    (무한성 캡: 자동 반복 없음).
    """
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
        gate = review.gate
        # 자료 풀 확정: 사람이 제외한 출처를 같은 커밋에서 is_included=false로 반영한다.
        if gate == ReviewGate.SOURCE_POOL.value:
            n_excluded = await _apply_source_pool_exclusions(session, project_id, decision)
            if n_excluded:
                logger.info("source_pool.pruned", project_id=str(project_id), excluded=n_excluded)
        await session.commit()
    if gate == ReviewGate.SOURCE_POOL.value and decision.get("action") == "collect_more":
        _spawn_collect_more(project_id)
        return
    _spawn_guarded(project_id)


def _source_ref_from_row(row: ProjectSource) -> SourceRef:
    """project_sources 행 → 게이트 payload용 SourceRef (metadata_ 신호 복원)."""
    from src.workflows.stages import _source_preview, has_usable_content, relevance_excerpt

    meta = row.metadata_ or {}
    content_md = meta.get("content_md") or ""
    usable = has_usable_content(content_md)
    matched = list(meta.get("matched_sections") or [])
    return SourceRef(
        id=row.id,
        source_type=SourceType(row.source_type),
        title=row.title or row.url or "(제목 없음)",
        url=row.url,
        reliability=row.reliability,
        matched_sections=list(meta.get("matched_sections") or []),
        page_age=meta.get("page_age"),
        preview=(relevance_excerpt(content_md, matched) or _source_preview(content_md))
        if usable
        else None,
        has_content=usable,
    )


def _spawn_collect_more(project_id: uuid.UUID) -> bool:
    """보충 수집 라운드를 중복 가드 + 전역 슬롯 하에 spawn."""
    return _spawn_limited(project_id, lambda: _collect_more(project_id), clear_cancel_on_exit=False)


async def _collect_more(project_id: uuid.UUID) -> None:
    """SOURCE_POOL '추가 조사' — 기존 풀 유지 + research_more_batch건 보충 + 게이트 재개방.

    write로 전진하지 않는다: 새로 모은 출처를 기존 풀에 합쳐(URL 중복 제거)
    새 SOURCE_POOL 게이트를 만들고 다시 사람 판단을 기다린다. 사람이 누를
    때마다 한 라운드씩이라 무한성 캡은 사람 손에 있다.
    """
    from src.workflows.pipeline import _source_pool_gate
    from src.workflows.stages import _collect_sources, source_dedup_key

    owner_id: uuid.UUID | None = None
    try:
        async with async_session_maker() as session:
            project = await session.get(Project, project_id)
            if project is None:
                logger.warning("project.missing", project_id=str(project_id))
                return
            owner_id = project.owner_id
            state = _state_from_project(project)
            state = await _rehydrate_section_plan(session, project.id, state)
            rows = (
                (
                    await session.execute(
                        select(ProjectSource).where(ProjectSource.project_id == project_id)
                    )
                )
                .scalars()
                .all()
            )
        existing_refs = [_source_ref_from_row(r) for r in rows]
        # 본문 있는 출처만 재수집에서 제외 — 본문 없는 껍데기는 다시 회수될 기회를
        # 준다(성공하면 indexer.stage 업서트로 같은 행이 실자료로 승격, id 유지).
        exclude = {
            key
            for r in existing_refs
            if r.has_content and (key := source_dedup_key(r.url, r.title))
        }

        new_refs = await _collect_sources(
            state,
            exclude_keys=exclude,
            target=settings.research_more_batch,
            ensure_coverage=False,
        )
        # 재회수로 승격된 출처는 id가 기존 행과 같다 — 껍데기 버전을 빼고 병합해
        # 게이트 payload에 같은 자료가 두 번 실리지 않게 한다.
        new_ids = {r.id for r in new_refs}
        state = state.add_sources([r for r in existing_refs if r.id not in new_ids] + new_refs)
        logger.info(
            "source_pool.collect_more",
            project_id=str(project_id),
            n_new=len(new_refs),
            n_total=len(state.sources),
        )

        review = _source_pool_gate(state)
        async with async_session_maker() as session:
            session.add(
                ReviewPoint(
                    id=review.id,
                    project_id=project_id,
                    gate=review.gate.value,
                    payload=review.payload,
                    status="pending",
                    created_at=review.created_at,
                )
            )
            await session.commit()
        emit_checkpoint(project_id, str(review.id), gate_level(review.gate.value))
        if owner_id is not None:
            await _notify_safe(owner_id, project_id, "partial", page="progress")
    except Exception:
        logger.exception("source_pool.collect_more_failed", project_id=str(project_id))
        emit_error(project_id, "collect_more_failed", "추가 조사 중 오류가 발생했습니다")
