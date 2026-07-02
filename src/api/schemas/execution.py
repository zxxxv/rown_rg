from __future__ import annotations

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


class DecideRequest(BaseModel):
    """검토 게이트 결정 — review_point에 기록되고 척추가 다음 단계부터 재개된다."""

    decision: dict[str, Any] = Field(
        default_factory=dict,
        description='사용자 결정(예: {"approved": true, "selected_ids": [...]})',
    )
