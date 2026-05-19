from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.chunk import Chunk
    from src.db.models.library_node import LibraryNode
    from src.db.models.project import Project


class ProjectSource(Base):
    __tablename__ = "project_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    library_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("library_nodes.id", ondelete="SET NULL")
    )
    upload_path: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(Text)
    reliability: Mapped[str | None] = mapped_column(String(10))
    is_included: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False, index=True
    )
    metadata_: Mapped[dict] = mapped_column(  # type: ignore[assignment]
        "metadata", JSONB, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    project: Mapped[Project] = relationship(back_populates="sources", lazy="raise")
    library_node: Mapped[LibraryNode | None] = relationship(lazy="raise")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="source", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('library', 'upload', 'web_search')",
            name="project_sources_source_type_check",
        ),
        CheckConstraint(
            "reliability IN ('high', 'medium', 'low')",
            name="project_sources_reliability_check",
        ),
        # 부분 UNIQUE — library/upload UPSERT 키. web_search는 두 컬럼 모두 NULL이라 매번 INSERT.
        Index(
            "uq_project_sources_project_library",
            "project_id",
            "library_node_id",
            unique=True,
            postgresql_where="library_node_id IS NOT NULL",
        ),
        Index(
            "uq_project_sources_project_upload",
            "project_id",
            "upload_path",
            unique=True,
            postgresql_where="upload_path IS NOT NULL",
        ),
    )
