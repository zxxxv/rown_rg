from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.api.schemas.project import ProjectStatus


class RunResponse(BaseModel):
    """실행 시작/재개 직후 응답(백그라운드로 진행 중)."""

    project_id: str
    status: ProjectStatus


class ProgressResponse(BaseModel):
    """프로젝트 진행 상황 조회 응답."""

    project_id: str
    status: ProjectStatus
    # 검토 게이트에서 대기 중이면 그 payload(gate·message·candidates 등), 아니면 None
    pending_gate: dict[str, Any] | None = None
    # 단계 기반 근사 진행률(0~100) — 세밀한 섹션 단위 진행은 추후 WS/이벤트로
    percent: int = 0
    # 이 프로젝트의 누적 토큰·비용 (token_usage 합산, 프론트 CostTracker용)
    tokens_used: int = 0
    cost_usd: float = 0.0
    # 실행 시작·마지막 활동 시각 — 별도 컬럼 없이 token_usage 최초/최종 기록으로 근사.
    # (첫 LLM 콜은 실행 시작 수 초 내라 경과 시간 표시엔 충분) 기록 없으면 None.
    started_at: datetime | None = None
    last_activity_at: datetime | None = None
    # 전역 동시 실행 상한 대기열 위치(1부터) — 대기 중이 아니면 None
    queue_position: int | None = None
    # 실행 중인 세부 단계 라벨(예: "청킹·임베딩 5/17", "배경 요약 1층 · 40/152") —
    # 색인·RAPTOR처럼 수 분짜리 단계의 내부 진행을 스테퍼 서브라벨로 보여준다.
    # 실행 중이 아니거나 세부 단계가 없으면 None.
    active_step: str | None = None
    # 진행 중 세부 단계 전부(시작 순) — 병렬 작성의 절 4개가 다 보이게. active_step은
    # 마지막 하나만 담는 구계약이라 유지(프론트 구버전 호환).
    active_steps: list[str] | None = None
    # 순수 생성 시간(초) — 사람 검토 대기·중단 구간을 뺀 실제 작업 시간.
    # 벽시계 경과(started_at~last_activity_at)와 달리 게이트에서 멈춘다.
    active_seconds: int = 0
    # 자료 수집 목표 건수(권장 하한, settings.research_min_sources) — 수집 중 배너가
    # "현재 n건 / 목표 m건"을 보여줄 수 있게 게이트 전에도 내려준다(게이트 payload의
    # coverage.min_required와 같은 값).
    source_target: int = 0
    # 이 프로세스에 살아 있는 러너 태스크가 있는지(단일 워커 전제). 작업 단계인데
    # 게이트 대기도 아니고 이 값도 False면 죽은 런이다 — 프로세스 재시작·자원 고갈로
    # 태스크가 사라져도 status는 그 단계에 남아, 이 신호 없인 스피너가 영원히 돈다
    # (2026-08-12 실사용: 3시간 넘게 '진행 중'). /run으로 이어서 재개할 수 있다.
    runner_alive: bool = True
    # 마지막 진행 이벤트(emit_*) 시각 — 인메모리라 재시작 후 None. 살아 있는데 오래
    # 조용한 런(외부 콜 대기 고착)을 화면이 알아볼 수 있게 내려준다.
    last_event_at: datetime | None = None


class DecideRequest(BaseModel):
    """검토 게이트 결정 — review_point에 기록되고 척추가 다음 단계부터 재개된다."""

    decision: dict[str, Any] = Field(
        default_factory=dict,
        description='사용자 결정(예: {"approved": true, "selected_ids": [...]})',
    )
