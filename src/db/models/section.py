from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.project import Project

_QA_STATUSES = ("passed", "failed", "pending")
_SECTION_STATUSES = ("pending", "writing", "completed", "failed")


class Section(Base):
    """완료된 보고서의 섹션 1개 — 작성 확정 시(assemble) 영구 저장.

    ProjectState/review_points는 실행 중 인메모리·게이트 payload라 사후 조회가 안 된다.
    이 테이블이 '완료 보고서를 섹션 단위로 열람·편집'하는 유일한 조회원이다.
    id는 SectionPlan.section_id를 그대로 쓴다(프론트 트리 노드 id ↔ 본문 조회 키 일치).
    """

    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_title: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 트리 깊이 — 현재는 모두 2(장.절). 향후 하위 절 확장 여지로 컬럼화.
    level: Mapped[int] = mapped_column(Integer, server_default="2", nullable=False)
    content: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    source_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}", nullable=False
    )
    # 생성 지표(opaque) — evidence_count(검색된 인용 가능 청크 수),
    # volume_scaled(재료 부족으로 분량 목표를 내렸는지). 본문을 더럽히지 않고
    # 화면에서 '자료 부족' 배지로 알리기 위한 자리다.
    # 이 본문을 쓸 때의 계획 지문(services/sections/drift.content_fingerprint).
    # 현재 목차 정본의 지문과 다르면 "목차 수정 미반영"이다. 빈 문자열 = 지문 기록
    # 이전에 쓰인 옛 절이라 판정에서 제외한다(0047).
    plan_hash: Mapped[str] = mapped_column(String(40), server_default="", nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False, default=dict)
    qa_status: Mapped[str] = mapped_column(String(10), server_default="pending", nullable=False)
    status: Mapped[str] = mapped_column(String(10), server_default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    project: Mapped[Project] = relationship(back_populates="sections", lazy="raise")

    __table_args__ = (
        UniqueConstraint("project_id", "chapter_number", "section_number", name="uq_section_pos"),
        CheckConstraint(
            f"qa_status IN {_QA_STATUSES}",
            name="sections_qa_status_check",
        ),
        CheckConstraint(
            f"status IN {_SECTION_STATUSES}",
            name="sections_status_check",
        ),
    )
