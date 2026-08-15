"""보고서 파이프라인 척추 — 수동 오케스트레이터 (LangGraph 미사용).

ProjectState를 단계 함수에 순서대로 흘려보내고, 사람 검토 게이트에서 멈춘다.
이 모듈은 **순수**다 — DB I/O·영속화는 runner가 담당한다(테스트 용이).

resume 계약: 현재 위치는 state.current_stage가 단일 진실. 게이트에서 멈출 때
work 단계까지 advance해 두고(예: research 끝 → RESEARCHING), 재개 시 같은 상태로
advance를 다시 부르면 다음 단계가 이어진다. "검토 대기"는 status 값이 아니라
pending review_point의 존재로 표현한다(척추 위치를 모호하지 않게).

config-driven 확장: PHASES를 ProjectConfig 토글로 조립하면 사용자가 고른 단계만
실행된다(지금은 고정 2단계 스켈레톤).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import ProjectStage, ReviewGate, UserReviewPoint
from src.services.generation.design_brief import build_design_brief
from src.workflows import cancel
from src.workflows.stages import assemble, collect, index, plan_brief, write
from src.workflows.write_loop import section_plan_payload


@dataclass(frozen=True)
class Paused:
    """
    게이트에서 멈춤 - 사용자 결정을 기다린다.
    """

    state: ProjectState
    review: UserReviewPoint


@dataclass(frozen=True)
class Done:
    """파이프라인 완주."""

    state: ProjectState


Outcome = Paused | Done


def _design_brief_gate(state: ProjectState) -> UserReviewPoint:
    """수집 전 설계 확인 게이트 — 절별 검색 질의·분량·배정을 사람이 보고 고친다.

    payload에 plan을 실을 필요가 없다. 결정은 config.outline에 커밋되고 plan 정본은
    config["_section_plan"]에 있어서, 재개는 그 둘만 보면 된다(core.section_plan).

    브리프 본체는 plan_brief 단계가 만든다(개인 에이전트 카탈로그가 DB라 async 필요).
    여기 폴백은 그 단계가 브리프를 못 실었을 때의 최소 보장 — 분량 목표만 파일
    카탈로그 기준으로 좁아진다.
    """
    return UserReviewPoint(
        gate=ReviewGate.DESIGN_BRIEF,
        payload=state.design_brief or build_design_brief(state.section_plan, topic=state.topic),
    )


def _source_pool_gate(state: ProjectState) -> UserReviewPoint:
    """자료 풀 + 목차를 함께 검토하는 게이트.

    section_plan을 payload에 싣는 이유: plan은 projects 테이블에 없어, resume 시
    runner가 이 payload에서 복원한다(QA_SELECT 게이트와 같은 계약).
    """
    # 커버리지는 본문 있는 자료만 센다 — 본문 없는 출처(URL만)는 검색 근거가
    # 못 되므로 총량에 넣으면 "44건인데 쓸 건 5건"이 충분으로 위장된다(2026-08-03 실측).
    usable = [s for s in state.sources if s.has_content]
    n_usable = len(usable)
    # 절별 커버리지 — 매칭 자료(matched_sections)가 하나도 없는 절을 표면화한다.
    # 5.2 '해외 사례'처럼 자료 풀에 해당 절 재료가 아예 없으면 작성이 빈약해지거나
    # 엉뚱한 자료로 채워진다(2026-08-05 숏폼 실측) — 추가 검색을 유도하는 신호.
    matched_titles = {t for s in usable for t in (s.matched_sections or [])}
    uncovered = [
        f"{p.chapter_number}.{p.section_number} {p.title}"
        for p in state.section_plan
        if p.title not in matched_titles
    ]
    return UserReviewPoint(
        gate=ReviewGate.SOURCE_POOL,
        payload={
            "message": "목차와 수집된 자료 풀을 검토·승인하세요.",
            "section_plan": section_plan_payload(state.section_plan),
            "sources": [s.model_dump(mode="json") for s in state.sources],
            # 자료량 신호 — 미달은 차단이 아니라 '추가 조사' 유도(사람 판단).
            "coverage": {
                "n_sources": n_usable,
                "min_required": settings.research_min_sources,
                "sufficient": n_usable >= settings.research_min_sources,
                "uncovered_sections": uncovered,
            },
        },
    )


def _rehearsal_gate(state: ProjectState) -> UserReviewPoint | None:
    """색인 끝 검색 리허설의 조건부 게이트 — 근거 공백 절이 있으면 자료 게이트 재개방.

    리허설(_rehearse)이 판정을 이미 끝냈고 여기는 그 결과(state.rehearsal.reopen)를
    게이트로 옮기기만 한다 — 순수 함수 계약 유지. 게이트 종류는 새로 만들지 않고
    SOURCE_POOL을 다시 연다: 사람이 할 일(자료 보강·제외·추가 조사)이 그 화면 그대로고,
    resume 경로도 기존 계약을 탄다(runner가 INDEXING 상태의 SOURCE_POOL 확정을
    RESEARCHING으로 되돌려 재색인·재리허설을 돌린다).

    payload.rehearsal에 공백 절 목록을 실어 화면이 '왜 다시 열렸는지'를 보여준다.
    구성형 절(앞 절 산출로 쓰는 절)은 자료로 못 채우므로 재개방 사유가 아니라
    구분된 경고로만 싣는다.
    """
    report = state.rehearsal or {}
    if not report.get("reopen"):
        return None
    review = _source_pool_gate(state)
    empty = [s for s in report.get("sections", []) if s.get("band") == "empty"]
    payload = dict(review.payload)
    payload["message"] = (
        "검색 리허설에서 근거가 부족한 절이 발견됐습니다 - 자료를 보강하거나 그대로 진행하세요."
    )
    payload["rehearsal"] = {
        "reopens_used": report.get("reopens_used", 0),
        "empty_sections": [
            {
                "label": s.get("label", ""),
                "floor_passed": s.get("floor_passed", 0),
                "needed": s.get("needed", 0),
                "constructive": bool(s.get("constructive")),
                "raptor_gap": bool(s.get("raptor_gap")),
            }
            for s in empty
        ],
    }
    return UserReviewPoint(gate=ReviewGate.SOURCE_POOL, payload=payload)


@dataclass(frozen=True)
class Phase:
    """단계 1개: current_stage == when일 때 run을 실행하고 advance_to로 전이한다.

    running은 이 단계가 실행되는 동안의 표시용 상태 — 척추는 구간이 끝나야 상태를
    저장하므로, 긴 구간(색인→작성) 동안 UI가 옛 위치를 가리키는 문제를 훅으로 푼다.

    gate가 None을 반환하면 멈추지 않고 다음 단계로 계속 간다 — 조건부 게이트
    (검색 리허설의 자료 게이트 재개방처럼 '문제가 있을 때만 멈춤') 용.
    """

    when: ProjectStage
    run: Callable[[ProjectState], Awaitable[ProjectState]]
    advance_to: ProjectStage
    running: ProjectStage
    gate: Callable[[ProjectState], UserReviewPoint | None] | None = None


# 척추 단계 정의. collect→(자료 승인)→index→write→assemble→완료.
# 임베딩(index)은 자료 승인 게이트 '뒤'에 온다 — 채택된 자료만 임베딩해 비용을 아낀다.
# QA 게이트는 제거됨(2026-08-07 사용자 결정): n=1 전환으로 '고르기'가 사라졌고 통합
# 검토 화면이 완성 후 편집·재작성을 다 하므로, 검토는 사후·게이트는 무의미했다.
# write가 생존 후보를 자동 채택하고 곧장 조립한다(레거시 pending 게이트는 runner가 소화).
PHASES: list[Phase] = [
    # 설계 브리프는 '수집 전' 게이트지만 pre-gate가 아니다 — advance_to로 PLANNING에
    # 먼저 전이한 뒤 멈춘다. 그래야 재개할 때 when==CREATED가 다시 매칭되지 않는다
    # (stage 자체가 '이 게이트는 지났다'는 마커다). pre_gate로 만들면 그 마커가 없어
    # 게이트가 무한 재개방되고, 막으려면 순수한 척추가 review_points를 조회해야 한다.
    Phase(
        ProjectStage.CREATED,
        plan_brief,
        ProjectStage.PLANNING,
        running=ProjectStage.PLANNING,
        gate=_design_brief_gate,
    ),
    Phase(
        ProjectStage.PLANNING,
        collect,
        ProjectStage.RESEARCHING,
        running=ProjectStage.RESEARCHING,
        gate=_source_pool_gate,
    ),
    Phase(
        ProjectStage.RESEARCHING,
        index,
        ProjectStage.INDEXING,
        running=ProjectStage.INDEXING,
        # 조건부 — 색인 끝 검색 리허설이 근거 공백 절을 찾으면 자료 게이트를 다시
        # 연다(프로젝트당 2회까지). 없으면 None이라 멈추지 않고 write로 간다.
        gate=_rehearsal_gate,
    ),
    Phase(
        ProjectStage.INDEXING,
        write,
        ProjectStage.REVIEWING,
        running=ProjectStage.WRITING,
        gate=None,
    ),
    Phase(
        ProjectStage.REVIEWING,
        assemble,
        ProjectStage.COMPLETED,
        running=ProjectStage.REVIEWING,
        gate=None,
    ),
]

OnStage = Callable[[ProjectStage], Awaitable[None]]


async def advance(state: ProjectState, *, on_stage: OnStage | None = None) -> Outcome:
    """현재 단계부터 게이트 또는 완료까지 전진한다.

    on_stage: 각 단계 실행 직전에 '지금 실행 중인 단계'(Phase.running)를 알리는 훅.
    runner가 표시용 상태 영속화에 쓴다 — 실패해도 척추 진행에 영향 없어야 한다(호출부 책임).
    """
    while True:
        # 단계 경계 취소 지점 — 사용자가 취소를 요청했으면 여기서 RunCancelled로 중단.
        cancel.raise_if_cancelled(state.project_id)
        phase = next((p for p in PHASES if p.when == state.current_stage), None)
        if phase is None:
            return Done(state)
        if on_stage is not None:
            await on_stage(phase.running)
        state = await phase.run(state)
        state = state.with_stage(phase.advance_to)
        if phase.gate is not None:
            review = phase.gate(state)
            if review is not None:
                return Paused(state.with_pending_review(review), review)
