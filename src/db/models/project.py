from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.chunk import Chunk
    from src.db.models.consistency_graph_node import ConsistencyGraphNode
    from src.db.models.project_source import ProjectSource
    from src.db.models.raptor_node import RaptorNode
    from src.db.models.token_usage import TokenUsage
    from src.db.models.user import User


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    preset: Mapped[str | None] = mapped_column(String(100))
    config: Mapped[dict] = mapped_column(  # type: ignore[assignment]
        JSONB, server_default="{}", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), server_default="created", nullable=False, index=True
    )
    depth_mode: Mapped[str] = mapped_column(
        String(20), server_default="full_report", nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped[User] = relationship(back_populates="projects", lazy="raise")
    sources: Mapped[list[ProjectSource]] = relationship(back_populates="project", lazy="raise")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="project", lazy="raise")
    raptor_nodes: Mapped[list[RaptorNode]] = relationship(back_populates="project", lazy="raise")
    consistency_graph_nodes: Mapped[list[ConsistencyGraphNode]] = relationship(
        back_populates="project", lazy="raise"
    )
    token_usages: Mapped[list[TokenUsage]] = relationship(back_populates="project", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'researching', 'indexing', 'writing', "
            "'reviewing', 'completed', 'archived')",
            name="projects_status_check",
        ),
        CheckConstraint(
            "depth_mode IN ('outline_only', 'standard', 'full_report', 'deep_dive')",
            name="projects_depth_mode_check",
        ),
        Index("ix_projects_config", "config", postgresql_using="gin"),
    )
