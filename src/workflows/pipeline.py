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
from src.workflows import cancel
from src.workflows.stages import assemble, collect, index, write
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


@dataclass(frozen=True)
class Phase:
    """단계 1개: current_stage == when일 때 run을 실행하고 advance_to로 전이한다.

    running은 이 단계가 실행되는 동안의 표시용 상태 — 척추는 구간이 끝나야 상태를
    저장하므로, 긴 구간(색인→작성) 동안 UI가 옛 위치를 가리키는 문제를 훅으로 푼다.
    """

    when: ProjectStage
    run: Callable[[ProjectState], Awaitable[ProjectState]]
    advance_to: ProjectStage
    running: ProjectStage
    gate: Callable[[ProjectState], UserReviewPoint] | None = None


# 척추 단계 정의. collect→(자료 승인)→index→write→assemble→완료.
# 임베딩(index)은 자료 승인 게이트 '뒤'에 온다 — 채택된 자료만 임베딩해 비용을 아낀다.
# QA 게이트는 제거됨(2026-08-07 사용자 결정): n=1 전환으로 '고르기'가 사라졌고 통합
# 검토 화면이 완성 후 편집·재작성을 다 하므로, 검토는 사후·게이트는 무의미했다.
# write가 생존 후보를 자동 채택하고 곧장 조립한다(레거시 pending 게이트는 runner가 소화).
PHASES: list[Phase] = [
    Phase(
        ProjectStage.CREATED,
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
        gate=None,
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
            return Paused(state.with_pending_review(review), review)
