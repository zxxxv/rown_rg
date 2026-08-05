from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    event,
    func,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from src.core.types import ProjectStage
from src.db.base import Base

_STATUS_IN_CLAUSE = ", ".join(f"'{stage.value}'" for stage in ProjectStage)

if TYPE_CHECKING:
    from src.db.models.chunk import Chunk
    from src.db.models.consistency_graph_node import ConsistencyGraphNode
    from src.db.models.project_source import ProjectSource
    from src.db.models.raptor_node import RaptorNode
    from src.db.models.section import Section
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
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def owner_name(self) -> str | None:
        """소유자 이름(응답 표시용) — owner가 eager-load된 경우에만 값이 있다.

        lazy="raise" 관계라 미로드 접근은 예외이므로, 로드 여부를 __dict__로 확인한다.
        """
        if "owner" not in self.__dict__:
            return None
        return self.owner.name

    # Relationships
    owner: Mapped[User] = relationship(back_populates="projects", lazy="raise")
    sources: Mapped[list[ProjectSource]] = relationship(back_populates="project", lazy="raise")
    chunks: Mapped[list[Chunk]] = relationship(back_populates="project", lazy="raise")
    raptor_nodes: Mapped[list[RaptorNode]] = relationship(back_populates="project", lazy="raise")
    consistency_graph_nodes: Mapped[list[ConsistencyGraphNode]] = relationship(
        back_populates="project", lazy="raise"
    )
    token_usages: Mapped[list[TokenUsage]] = relationship(back_populates="project", lazy="raise")
    sections: Mapped[list[Section]] = relationship(back_populates="project", lazy="raise")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_STATUS_IN_CLAUSE})",
            name="projects_status_check",
        ),
        CheckConstraint(
            "depth_mode IN ('outline_only', 'standard', 'full_report', 'deep_dive')",
            name="projects_depth_mode_check",
        ),
        Index("ix_projects_config", "config", postgresql_using="gin"),
    )


def _sync_completed_at(mapper: Mapper, connection: Connection, target: Project) -> None:
    history = inspect(target).attrs.status.history
    if not history.has_changes():
        return

    previous_status = history.deleted[0] if history.deleted else None
    current_status = target.status
    if current_status == previous_status:
        return

    if current_status == ProjectStage.COMPLETED.value:
        target.completed_at = datetime.now(UTC)
    elif previous_status == ProjectStage.COMPLETED.value:
        target.completed_at = None


event.listen(Project, "before_insert", _sync_completed_at)
event.listen(Project, "before_update", _sync_completed_at)
