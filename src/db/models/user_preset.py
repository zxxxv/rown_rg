from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class UserPreset(Base):
    """개인 목차 프리셋 — 사용자가 저장한 보고서 구성(장·절·방향·에이전트 배정).

    시스템 프리셋(src/prompts/presets 파일)이 단일 진실인 카탈로그 위에 얹는
    개인 오버레이다(user_prompts와 같은 패턴). 같은 보고서 구성으로 여러 대상을
    분석하는 용례(2026-08-12 QA)에서 구성을 프로젝트 간에 재사용하게 한다.

    outline은 프리셋 상세와 같은 와이어 모양을 그대로 담는다:
    {"chapters": [{"title", "sections": [{"title", "direction", "key_points", "agents"}]}]}
    — 생성 화면이 시스템 프리셋과 동일한 코드로 골격을 로드한다.
    """

    __tablename__ = "user_presets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    outline: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_user_presets_owner_name"),)
