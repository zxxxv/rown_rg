from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.ip_whitelist import IpWhitelist
    from src.db.models.library_node import LibraryNode
    from src.db.models.project import Project
    from src.db.models.token_usage import TokenUsage


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False, index=True
    )
    totp_secret: Mapped[str | None] = mapped_column(String(32))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    projects: Mapped[list[Project]] = relationship(back_populates="owner", lazy="raise")
    ip_whitelists: Mapped[list[IpWhitelist]] = relationship(back_populates="creator", lazy="raise")
    library_nodes: Mapped[list[LibraryNode]] = relationship(back_populates="creator", lazy="raise")
    token_usages: Mapped[list[TokenUsage]] = relationship(back_populates="user", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'admin', 'worker', 'viewer')",
            name="users_role_check",
        ),
    )
