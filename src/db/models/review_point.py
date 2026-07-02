from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.types import ReviewGate
from src.db.base import Base

# gate 체크 제약은 ReviewGate(core)를 단일 진실로 삼아 파생한다.
_GATE_IN = ", ".join(f"'{g.value}'" for g in ReviewGate)


class ReviewPoint(Base):
    """사람 검토 게이트의 영속 기록.

    수동 척추가 게이트에서 멈추면 pending 행을 쓰고, decide로 재개될 때 resolved로
    바꾸며 결정을 기록한다. status는 라이프사이클 단일 진실(projects.status)이 아니라
    "이 프로젝트가 어느 게이트에서 대기 중인가"를 담는다(감사용 이력도 겸함).
    """

    __tablename__ = "review_points"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    gate: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), server_default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"gate IN ({_GATE_IN})", name="review_points_gate_check"),
        CheckConstraint("status IN ('pending', 'resolved')", name="review_points_status_check"),
        Index("ix_review_points_project_status", "project_id", "status"),
    )
