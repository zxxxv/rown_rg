from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class LibraryNode(Base):
    __tablename__ = "library_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("library_nodes.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    metadata_: Mapped[dict] = mapped_column(  # type: ignore[assignment]
        "metadata", JSONB, server_default="{}", nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    visible_to_users: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}", nullable=False
    )
    visible_to_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    parent: Mapped[LibraryNode | None] = relationship(
        back_populates="children", remote_side=[id], lazy="raise"
    )
    children: Mapped[list[LibraryNode]] = relationship(back_populates="parent", lazy="raise")
    creator: Mapped[User | None] = relationship(back_populates="library_nodes", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "type IN ('folder', 'file')",
            name="library_nodes_type_check",
        ),
        Index("ix_library_nodes_metadata", "metadata", postgresql_using="gin"),
        Index(
            "ix_library_nodes_visible_to_users",
            "visible_to_users",
            postgresql_using="gin",
        ),
        Index(
            "ix_library_nodes_visible_to_roles",
            "visible_to_roles",
            postgresql_using="gin",
        ),
    )
