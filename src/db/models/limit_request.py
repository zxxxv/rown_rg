from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class LimitRequest(Base):
    """사용자의 월 한도 증액 요청.

    사용자가 `amount_usd`(증액분)를 사유와 함께 신청하면 관리자가 승인/거절한다.
    승인 시 해당 사용자의 `user_quotas.monthly_limit_usd`가 증액분만큼 올라간다.

    테이블명은 legacy 이름 `quota_requests`를 유지한다(마이그레이션 회피 — 물리명만 quota).
    """

    __tablename__ = "quota_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending", nullable=False, index=True
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships — users.id로 향하는 FK가 둘(user_id, decided_by)이라 명시적으로 지정한다.
    user: Mapped[User] = relationship(
        back_populates="limit_requests", lazy="raise", foreign_keys=[user_id]
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="quota_requests_status_check",
        ),
        Index("ix_quota_requests_status_requested", "status", "requested_at"),
    )
