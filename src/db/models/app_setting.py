from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

if TYPE_CHECKING:
    pass


class AppSetting(Base):
    """관리자가 UI에서 채우는 운영 설정·시크릿(.env 오버라이드).

    시크릿(is_secret=True)은 value에 암호문(src.core.secrets)으로 저장하고,
    응답에서는 절대 평문을 돌려주지 않는다(설정됨/미설정만 노출). key는 Settings의
    속성명과 동일해 effective()가 env로 폴백할 수 있다.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)  # 시크릿이면 암호문
    is_secret: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
