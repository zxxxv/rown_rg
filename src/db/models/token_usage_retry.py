from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TokenUsageRetry(Base):
    """record_usage 저장 실패 시 재시도 대상을 담는 outbox 테이블.

    payload는 record_usage 호출에 필요한 원본 인자를 JSON으로 그대로 보관한다
    (user_id/project_id는 문자열 UUID, cost_usd는 문자열 — 둘 다 JSON 기본 타입이
    아니라서 직렬화 시 문자열로 변환해 담는다). user/project FK를 걸지 않는 이유는
    이 테이블이 감사·재처리용 outbox이기 때문 — 원본 사용자가 이후 삭제되어도
    재시도 기록 자체는 남아 있어야 한다.
    """

    __tablename__ = "token_usage_retry_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(10), server_default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="token_usage_retry_queue_status_check",
        ),
        Index("ix_token_usage_retry_queue_status_created", "status", "created_at"),
    )
