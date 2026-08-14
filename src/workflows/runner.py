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
from src.core.section_plan import SECTION_PLAN_KEY, config_with_plan
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

    page는 버튼이 여는 화면 — 문구가 "검토하러 가기"인데 검토 화면이 아닌 곳에 떨어지면
    안 되므로, 게이트 알림은 _GATE_PAGES로 결정 UI가 실제로 있는 화면을 넘긴다.
    /export·/progress는 폐지된 경로다(개요로 리다이렉트만 남음) — 쓰지 말 것.
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


# 게이트 → 결정 UI가 있는 화면. 자료 검토는 /sources, 본문 검토(QA)는 /preview에 통합됐다.
_GATE_PAGES = {"design_brief": "brief", "source_pool": "sources", "qa_select": "preview"}


async def _apply_design_brief(
    session: AsyncSession, project_id: uuid.UUID, decision: dict[str, Any]
) -> bool:
    """브리프 게이트 결정의 목차를 config.outline에 커밋. 바뀐 게 없으면 False.

    ⚠️ JSONB를 in-place로 고치면 SQLAlchemy가 dirty로 표시하지 않아 커밋해도 안 써진다.
    반드시 새 dict를 만들어 재할당한다(config_with_plan이 같은 이유로 새 dict를 준다).

    plan 정본은 함께 버린다 — 목차가 바뀌었는데 남겨 두면 collect가 '이미 plan이 있다'고
    보고 옛 목차로 실행한다(merge_config_update의 규칙과 같다).
    """
    outline = decision.get("outline")
    if not isinstance(outline, dict) or not outline.get("chapters"):
        return False
    project = await session.get(Project, project_id)
    if project is None:
        return False
    config = dict(project.config or {})
    if config.get("outline") == outline:
        return False
    config["outline"] = outline
    # 목차가 바뀌면 파생 스냅샷은 전부 버린다 — plan 정본도, 절별 실행 계획도
    # 옛 목차 기준이라 남겨 두면 새 목차와 어긋난 채 실행된다.
    config.pop(SECTION_PLAN_KEY, None)
    config.pop("_design_plan", None)
    project.config = config
    logger.info("design_brief.outline_updated", project_id=str(project_id))
    return True


# 계획 필드 하나의 상한 — 사람 편집이 열려 있으므로 폭주 입력을 프롬프트에 싣지 않는다.
_PLAN_FIELD_MAX_CHARS = 2000


async def _commit_design_plan(
    session: AsyncSession,
    project_id: uuid.UUID,
    payload: dict[str, Any] | None,
    decision: dict[str, Any] | None = None,
) -> None:
    """승인된 AI 실행 계획을 config["_design_plan"]에 커밋 — 작성 프롬프트 주입용.

    계획을 커밋하지 않으면 게이트가 보여준 계획은 안내문일 뿐이고 작성기는 그 계획을
    본 적 없는 채로 쓴다 — '공개한 대로 쓴다'가 성립하려면 계획이 계약이 되어야 한다.
    section_id 매핑은 config의 plan 정본(_section_plan)을 쓴다. 게이트에서 목차를
    고친 경우엔 호출하지 않는다(계획이 옛 목차 기준이라 주입하면 어긋난다).

    **사람이 게이트에서 고친 계획(decision["ai_plan"])이 AI 원안(payload)보다 우선한다**
    — 계획은 제안이고 확정은 사람 몫이라는 게이트 원칙 그대로. 원안은 payload에,
    수정본은 review.decision에 남아 무엇이 바뀌었는지 감사 이력도 유지된다.
    """
    from src.core.section_plan import plan_from_config

    override = (decision or {}).get("ai_plan")
    ai = (
        override
        if isinstance(override, dict) and isinstance(override.get("sections"), list)
        else (payload or {}).get("ai_plan")
    )
    sections = ai.get("sections") if isinstance(ai, dict) else None
    if not isinstance(sections, list) or not sections:
        return
    project = await session.get(Project, project_id)
    if project is None:
        return
    by_num = {
        (p.chapter_number, p.section_number): str(p.section_id)
        for p in plan_from_config(project.config)
    }
    notes: dict[str, dict[str, str]] = {}
    for s in sections:
        if not isinstance(s, dict):
            continue
        sid = by_num.get((s.get("chapter"), s.get("section")))
        if sid is None:
            continue
        note = {
            key: str(s.get(key) or "").strip()[:_PLAN_FIELD_MAX_CHARS]
            for key in ("goal", "source_strategy", "writing_plan")
        }
        if any(note.values()):
            notes[sid] = note
    if not notes:
        return
    # JSONB 재할당 필수 — in-place 수정은 SQLAlchemy가 dirty로 안 잡는다.
    project.config = {**(project.config or {}), "_design_plan": notes}
    logger.info("design_brief.plan_committed", project_id=str(project_id), n_sections=len(notes))


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


async def _normalize_resume_stage(
    session: AsyncSession, project_id: uuid.UUID, state: ProjectState
) -> ProjectState:
    """죽은 런 재개 시 게이트를 건너뛰지 않게 단계를 되돌린다.

    PLANNING·RESEARCHING은 '단계 실행 중'의 표시 상태이기도 하다 — plan_brief/collect
    도중 프로세스가 죽으면(재배포·--reload·강제 종료) status가 그 값으로 남는데, 그대로
    재개하면 advance가 "게이트 승인 완료"로 읽어 다음 단계로 건너뛴다. 실측(2026-08-15):
    수집 도중 --reload 재시작 → 재개가 자료 검토 게이트 없이 색인→작성으로 직행, 부분
    수집(13건)만으로 Opus 작성이 시작됐다.

    판정은 결정적이다: 그 단계의 게이트가 **resolved로 존재**해야 통과한 것이다.
    없으면 게이트를 만드는 단계로 되돌린다(이미 스테이징된 자료·plan 정본은 그대로라
    되돌려도 수집은 제외분 빼고 부족분만 채운다).
    """
    checks = {
        ProjectStage.PLANNING: (ReviewGate.DESIGN_BRIEF, ProjectStage.CREATED),
        ProjectStage.RESEARCHING: (ReviewGate.SOURCE_POOL, ProjectStage.PLANNING),
    }
    gate_and_fallback = checks.get(state.current_stage)
    if gate_and_fallback is None:
        return state
    gate, fallback = gate_and_fallback
    resolved = (
        await session.execute(
            select(ReviewPoint.id)
            .where(
                ReviewPoint.project_id == project_id,
                ReviewPoint.gate == gate.value,
                ReviewPoint.status == "resolved",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if resolved is not None:
        return state
    logger.warning(
        "project.resume_stage_rollback",
        project_id=str(project_id),
        from_stage=state.current_stage.value,
        to_stage=fallback.value,
        missing_gate=gate.value,
    )
    return state.with_stage(fallback)


def _state_from_project(project: Project) -> ProjectState:
    # projects.status는 '표시용 단계'도 담는다(Phase.running) — WRITING은 척추 단계가
    # 아니라 INDEXING 구간이 실행 중임을 뜻한다. 그대로 stage로 삼으면 매칭되는 Phase가
    # 없어 advance가 즉시 Done을 돌려주고, 작성 도중 죽은 런이 '완료'로 둔갑한다
    # (2026-08-09 실측: 메모리 고갈로 1.3절에서 멈춘 런). 소유 단계로 되돌려 복구한다.
    status = project.status
    # 취소된 런은 재개 지점을 잃는다(status=cancelled). 취소 시 기록해 둔 직전 단계로
    # 되돌려 이어서 돌린다 — 없으면 처음부터(created). 화면의 "다시 시작" 버튼과
    # 라우터 가드가 cancelled를 재개 가능으로 보므로 여기서도 살려야 한다(2026-08-10).
    if status == ProjectStage.CANCELLED.value:
        status = (project.config or {}).get("cancelled_from") or ProjectStage.CREATED.value
        if status not in {stage.value for stage in ProjectStage}:
            status = ProjectStage.CREATED.value
    if status == ProjectStage.WRITING.value:
        status = ProjectStage.INDEXING.value
    return ProjectState.from_db(
        {
            "id": project.id,
            "owner_id": project.owner_id,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "topic": project.topic,
            "title": project.title,
            "preset": project.preset,
            "depth_mode": project.depth_mode,
            "status": status,
            "config": project.config,
        }
    )


async def _rehydrate_section_plan(
    session: AsyncSession, project_id: uuid.UUID, state: ProjectState
) -> ProjectState:
    """구버전 폴백 — SOURCE_POOL review payload에서 section_plan을 되살린다.

    정본은 projects.config["_section_plan"]이고 _state_from_project가 이미 읽는다.
    이 경로는 그 필드가 생기기 전에 시작돼 지금 재개되는 런에만 걸린다 — state에 plan이
    있으면(정본이 있었다는 뜻) 즉시 반환하므로 새 런에는 쿼리조차 안 나간다.
    """
    # REVIEWING 포함: QA 게이트 제거 후 조립 중 크래시 복구는 sections 행+plan으로
    # 재구성한다(_rehydrate_qa_selection의 overlay가 행을 후보로 승격).
    if (
        state.current_stage
        not in (ProjectStage.RESEARCHING, ProjectStage.INDEXING, ProjectStage.REVIEWING)
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
    """resume 시 REVIEWING 단계의 조립 입력(후보·선택)을 state에 되살린다.

    QA 게이트 제거(2026-08-07) 후 정상 흐름은 write→assemble 인메모리 직결이라 이
    경로는 크래시 복구·레거시 재개용이다: ① 레거시 resolved QA_SELECT review가 있으면
    payload+선택 복원 ② sections 행(작업 사본)을 overlay — 행 내용이 항상 우선이고,
    후보가 없으면 행을 합성 후보로 승격해 sections만으로도 조립을 재구성한다.
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
    if review is not None:
        state = rehydrate_from_payload(state, review.payload)
        selections = (review.decision or {}).get("selections", {})
        if selections:
            state = apply_selection(state, selections)
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
            # 죽은 런 재개 가드 — 게이트가 resolved로 없으면 그 단계를 다시 돈다.
            state = await _normalize_resume_stage(session, project.id, state)
            if project.status == ProjectStage.CREATED.value:
                # 표시용 선행 전이: 실행에 들어간 순간부터 UI가 '시작 전'으로
                # 보이지 않게 researching으로 먼저 영속화한다. 척추 진행 판단은
                # 위에서 이미 만든 in-memory state(CREATED) 기준이라 단계를
                # 건너뛰지 않는다(엔드포인트 상태 선점 사고와 다른 지점).
                entered_from_created = True
                project.status = ProjectStage.PLANNING.value
                # 실행 시점 역할별 모델 스냅샷 — 모드→모델 매핑은 코드가 진실이라
                # 배포로 바뀌면 과거 런의 표시가 함께 바뀐다(2026-08-14 고급 수집
                # Haiku 환원 때 옛 Sonnet 수집 런이 'Haiku 수집'으로 둔갑).
                # 이 런이 실제로 쓴 조합을 config에 남겨 표시·기록을 고정한다.
                from src.workflows.stages import _models_for

                project.config = {**(project.config or {}), "models": _models_for(state)}
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
            # 수집을 앞둔 재개(CREATED·PLANNING)면 스테이징돼 있던 출처를 상태로 복원 —
            # collect가 기존 출처를 제외(중복 스테이징 방지)하고 모자란 만큼만 보충하며,
            # 게이트 payload에도 업로드·기존 수집분이 함께 실린다. entered_from_created만
            # 보던 시절엔 PLANNING 재개가 빈 상태로 수집을 돌아 목표 계산이 어긋나고
            # 게이트가 이번 콜 수집분만 보여줬다(2026-08-15 실측: 37건 중 17건만 집계).
            if (
                state.current_stage in (ProjectStage.CREATED, ProjectStage.PLANNING)
                and not state.sources
            ):
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
            # section_plan 정본을 config에 남긴다 — 이걸로 다음 구간이 게이트 payload를
            # 뒤지지 않고 그대로 이어받는다. **재할당이어야 한다**: JSONB를 in-place로
            # 고치면 SQLAlchemy가 dirty로 안 잡아 커밋해도 안 써진다(config_with_plan이
            # 항상 새 dict를 돌려주는 이유).
            project.config = config_with_plan(project.config, outcome.state.section_plan)

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
                # 검토 차례 알림 (partial=확인 필요 템플릿) — 버튼은 그 게이트의 결정 화면으로
                await _notify_safe(
                    owner_id,
                    project_id,
                    "partial",
                    page=_GATE_PAGES.get(outcome.review.gate.value, "overview"),
                )
            else:
                await session.commit()
                logger.info("project.completed", project_id=str(project_id))
                # 완료 알림 — 다운로드는 개요 헤더에 있다
                await _notify_safe(owner_id, project_id, "success", page="overview")
    except cancel.RunCancelled:
        # 사용자 취소 — 실패가 아니라 깨끗한 중단으로 CANCELLED 확정(created 복귀 로직 회피).
        logger.info("project.cancelled", project_id=str(project_id))
        try:
            async with async_session_maker() as recovery:
                cancelled = await recovery.get(Project, project_id)
                if cancelled is not None:
                    # 재개 지점 보존 — 이 값이 없으면 취소한 런은 처음부터 다시 돌려야 한다.
                    cancelled.config = {
                        **(cancelled.config or {}),
                        "cancelled_from": cancelled.status,
                    }
                    cancelled.status = ProjectStage.CANCELLED.value
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
        from src.core.exceptions import IncompleteReportError, QuotaExceededError

        if isinstance(exc, QuotaExceededError):
            emit_error(
                project_id, "quota_exceeded", f"{exc.message} - 한도 상향 후 다시 시작하세요"
            )
        elif isinstance(exc, IncompleteReportError):
            # 완성 게이트(2026-08-13) — 어느 절이 비었는지까지 그대로 알린다.
            # 완성분은 sections에 저장돼 있어 미리보기에서 열람·재작성 후 재시작하면 된다.
            emit_error(
                project_id,
                "sections_incomplete",
                f"{exc.message} - 미리보기에서 실패한 절을 채운 뒤 다시 시작하세요",
            )
        else:
            emit_error(project_id, "run_failed", "실행 중 오류가 발생했습니다")
        if owner_id is not None:
            # 실패 알림 — 상태·재시작은 개요 화면에 있다(/progress는 폐지된 경로)
            await _notify_safe(owner_id, project_id, "failed", page="overview")


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
        # 설계 브리프 확정: 사람이 고친 목차를 config.outline에 커밋한다. plan 정본은
        # 함께 버려서 collect가 새 목차로 다시 뽑게 한다 — 결정을 payload 복원에 기대지
        # 않으므로 재개 경로가 하나로 모인다.
        if gate == ReviewGate.DESIGN_BRIEF.value:
            outline_changed = await _apply_design_brief(session, project_id, decision)
            if decision.get("action") != "replan" and not outline_changed:
                # 그대로 승인 — 게이트가 보여준 AI 실행 계획(사람 수정본 우선)을 작성
                # 계약으로 커밋. 목차를 고친 채 곧장 진행하면 계획이 옛 목차 기준이라
                # 커밋하지 않는다(재계산 라운드를 돌면 새 계획이 커밋된다).
                await _commit_design_plan(session, project_id, review.payload, decision)
        # 자료 풀 확정: 사람이 제외한 출처를 같은 커밋에서 is_included=false로 반영한다.
        if gate == ReviewGate.SOURCE_POOL.value:
            n_excluded = await _apply_source_pool_exclusions(session, project_id, decision)
            if n_excluded:
                logger.info("source_pool.pruned", project_id=str(project_id), excluded=n_excluded)
        await session.commit()
    if gate == ReviewGate.DESIGN_BRIEF.value and decision.get("action") == "replan":
        # 고친 목차로 브리프를 다시 계산해 게이트를 다시 연다 — 수집으로 안 간다.
        # '추가 조사'와 같은 라운드 패턴: 사람이 누를 때마다 1회(무한성 캡은 사람 손에).
        _spawn_replan(project_id)
        return
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
    source_type = SourceType(row.source_type)
    # 업로드·라이브러리 자료는 본문 추출(파싱)이 색인 단계에서 일어난다 — 지금
    # content_md가 비어도 파일이 있으므로 '쓸 수 있는 자료'다. 웹처럼 회수 실패로
    # 치면 게이트가 "본문 있는 자료 부족"을 오경보한다(2026-08-15 실측: PDF 12건을
    # 올렸는데 전부 본문 없음으로 집계돼 17/40 경고).
    file_backed = source_type in (SourceType.UPLOAD, SourceType.LIBRARY)
    matched = list(meta.get("matched_sections") or [])
    return SourceRef(
        id=row.id,
        source_type=source_type,
        title=row.title or row.url or "(제목 없음)",
        url=row.url,
        reliability=row.reliability,
        matched_sections=list(meta.get("matched_sections") or []),
        page_age=meta.get("page_age"),
        preview=(relevance_excerpt(content_md, matched) or _source_preview(content_md))
        if usable
        else None,
        has_content=usable or file_backed,
    )


def _spawn_collect_more(project_id: uuid.UUID) -> bool:
    """보충 수집 라운드를 중복 가드 + 전역 슬롯 하에 spawn."""
    return _spawn_limited(project_id, lambda: _collect_more(project_id), clear_cancel_on_exit=False)


def _spawn_replan(project_id: uuid.UUID) -> bool:
    """설계 브리프 재계산 라운드를 중복 가드 + 전역 슬롯 하에 spawn."""
    return _spawn_limited(project_id, lambda: _replan(project_id), clear_cancel_on_exit=False)


async def _replan(project_id: uuid.UUID) -> None:
    """DESIGN_BRIEF '재계산' — 고친 목차로 브리프·AI 계획을 다시 만들어 게이트 재개방.

    척추를 전진시키지 않는다(수집 안 함). _apply_design_brief가 이미 config.outline을
    갱신하고 plan 정본을 버렸으므로, plan_brief가 새 목차에서 plan과 브리프를 다시
    만든다. 새 게이트가 열리면 사람이 다시 보고 확정한다.
    """
    from src.workflows.pipeline import _design_brief_gate
    from src.workflows.stages import plan_brief

    owner_id: uuid.UUID | None = None
    try:
        async with async_session_maker() as session:
            project = await session.get(Project, project_id)
            if project is None:
                logger.warning("project.missing", project_id=str(project_id))
                return
            owner_id = project.owner_id
            state = _state_from_project(project)
        state = await plan_brief(state)
        review = _design_brief_gate(state)
        async with async_session_maker() as session:
            project = await session.get(Project, project_id)
            if project is None:
                return
            # 새 plan 정본 영속화 — 재할당 필수(JSONB in-place는 dirty로 안 잡힌다).
            project.config = config_with_plan(project.config, state.section_plan)
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
            await _notify_safe(owner_id, project_id, "partial", page="brief")
    except Exception:
        logger.exception("design_brief.replan_failed", project_id=str(project_id))
        if owner_id is not None:
            await _notify_safe(owner_id, project_id, "failed", page="brief")


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

        # 자료가 한 건도 안 붙은 절을 겨냥한다 — 라운드를 거듭해도 이미 풍족한 절만
        # 다시 긁어와 dedup으로 버려지고, 빈 절은 끝까지 빈 채 남던 문제(2026-08-09).
        covered = {sec for r in existing_refs if r.has_content for sec in r.matched_sections}
        uncovered = {s.title for s in state.section_plan if s.title not in covered}
        if uncovered:
            logger.info(
                "source_pool.collect_more_focus",
                project_id=str(project_id),
                n_uncovered=len(uncovered),
                n_sections=len(state.section_plan),
            )

        new_refs = await _collect_sources(
            state,
            exclude_keys=exclude,
            target=settings.research_more_batch,
            ensure_coverage=False,
            focus_titles=uncovered or None,
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
            # 추가 조사 후 재검토 — 항상 자료 검토 게이트라 결정 화면(/sources)으로
            await _notify_safe(owner_id, project_id, "partial", page="sources")
    except Exception:
        logger.exception("source_pool.collect_more_failed", project_id=str(project_id))
        emit_error(project_id, "collect_more_failed", "추가 조사 중 오류가 발생했습니다")
