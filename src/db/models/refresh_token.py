from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    # id == JWT의 jti
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 회전 체인 식별자: 재사용 감지 시 일괄 폐기 단위
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 이미 회전(소비)되었는지 — True인 토큰의 재제출 = 재사용 공격 신호
    used: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    # 명시적 폐기(로그아웃·재사용 감지)
    revoked: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )
