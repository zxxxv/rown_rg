from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class UserQuota(Base):
    """사용자별 월 비용 한도(USD).

    행이 없으면 역할별 기본 한도(`src.core.quota.default_quota_for`)로 폴백한다
    → 한도 점진 도입 가능. 사용자당 1행(1:1).
    """

    __tablename__ = "user_quotas"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    monthly_limit_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    # 마지막으로 한도를 조정한 관리자(감사용). 사용자 삭제 시 한도 행은 남기고 출처만 비운다.
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships — users.id로 향하는 FK가 둘(user_id, updated_by)이라 명시적으로 지정한다.
    user: Mapped[User] = relationship(back_populates="quota", lazy="raise", foreign_keys=[user_id])
