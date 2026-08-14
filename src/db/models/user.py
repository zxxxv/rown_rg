from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.types import Role
from src.db.base import Base

_ROLE_IN_CLAUSE = ", ".join(f"'{role.value}'" for role in Role)

if TYPE_CHECKING:
    from src.db.models.ip_whitelist import IpWhitelist
    from src.db.models.library_node import LibraryNode
    from src.db.models.limit_request import LimitRequest
    from src.db.models.project import Project
    from src.db.models.token_usage import TokenUsage
    from src.db.models.user_limit import UserLimit


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # 로그인용 아이디(선택) — 이메일 대신 username으로도 로그인 가능
    username: Mapped[str | None] = mapped_column(String(50), unique=True)
    # SSO 전용 계정은 비밀번호가 없다 → nullable
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False, index=True
    )
    totp_secret: Mapped[str | None] = mapped_column(String(32))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 인증 통과 하트비트(60초 스로틀) — '현재 접속중' 판정용, last_login_at과 별개
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    @property
    def has_password(self) -> bool:
        """비밀번호 로그인 가능 여부(SSO 전용 계정은 False)."""
        return self.password_hash is not None

    # Relationships
    projects: Mapped[list[Project]] = relationship(back_populates="owner", lazy="raise")
    ip_whitelists: Mapped[list[IpWhitelist]] = relationship(back_populates="creator", lazy="raise")
    library_nodes: Mapped[list[LibraryNode]] = relationship(back_populates="creator", lazy="raise")
    token_usages: Mapped[list[TokenUsage]] = relationship(back_populates="user", lazy="raise")
    # 한도/증액요청 — users.id로 향하는 FK가 둘이라(updated_by·decided_by) FK를 명시한다.
    limit: Mapped[UserLimit | None] = relationship(
        back_populates="user",
        lazy="raise",
        uselist=False,
        foreign_keys="UserLimit.user_id",
    )
    limit_requests: Mapped[list[LimitRequest]] = relationship(
        back_populates="user",
        lazy="raise",
        foreign_keys="LimitRequest.user_id",
    )

    __table_args__ = (
        CheckConstraint(
            f"role IN ({_ROLE_IN_CLAUSE})",
            name="users_role_check",
        ),
    )
