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


class DecideRequest(BaseModel):
    """검토 게이트 결정 — review_point에 기록되고 척추가 다음 단계부터 재개된다."""

    decision: dict[str, Any] = Field(
        default_factory=dict,
        description='사용자 결정(예: {"approved": true, "selected_ids": [...]})',
    )
