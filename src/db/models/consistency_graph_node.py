from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.project import Project


class ConsistencyGraphNode(Base):
    __tablename__ = "consistency_graph_nodes"

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
    assertion_text: Mapped[str] = mapped_column(Text, nullable=False)
    assertion_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str | None] = mapped_column(String(255))
    source_reference: Mapped[str | None] = mapped_column(Text)
    is_user_decision: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False, index=True
    )
    asserted_in: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}", nullable=False)
    level: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    project: Mapped[Project] = relationship(back_populates="consistency_graph_nodes", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "assertion_type IN ('numeric', 'qualitative', 'source', 'decision')",
            name="consistency_graph_nodes_assertion_type_check",
        ),
        Index("ix_consistency_graph_nodes_project_subject", "project_id", "subject"),
        Index(
            "ix_consistency_graph_nodes_asserted_in",
            "asserted_in",
            postgresql_using="gin",
        ),
    )
