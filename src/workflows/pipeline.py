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

from src.core.state import ProjectState
from src.core.types import ProjectStage, ReviewGate, UserReviewPoint
from src.workflows.stages import research, write


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
    return UserReviewPoint(
        gate=ReviewGate.SOURCE_POOL,
        payload={
            "message": "수집된 자료 풀을 검토·승인하세요.",
            "sources": [s.model_dump(mode="json") for s in state.sources],
        },
    )


@dataclass(frozen=True)
class Phase:
    """단계 1개: current_stage == when일 때 run을 실행하고 advance_to로 전이한다."""

    when: ProjectStage
    run: Callable[[ProjectState], Awaitable[ProjectState]]
    advance_to: ProjectStage
    gate: Callable[[ProjectState], UserReviewPoint] | None = None


# 척추 단계 정의(스켈레톤). research·write가 LangGraph seam.
PHASES: list[Phase] = [
    Phase(ProjectStage.CREATED, research, ProjectStage.RESEARCHING, gate=_source_pool_gate),
    Phase(ProjectStage.RESEARCHING, write, ProjectStage.COMPLETED, gate=None),
]


async def advance(state: ProjectState) -> Outcome:
    """현재 단계부터 게이트 또는 완료까지 전진한다."""
    while True:
        phase = next((p for p in PHASES if p.when == state.current_stage), None)
        if phase is None:
            return Done(state)
        state = await phase.run(state)
        state = state.with_stage(phase.advance_to)
        if phase.gate is not None:
            review = phase.gate(state)
            return Paused(state.with_pending_review(review), review)
