from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class VerifyFinding(Base):
    """PM 검증 리포트 항목 — assemble 직후 챕터당 1콜 pm_verify의 경고(차단 아님).

    정적 게이트가 못 잡는 문서 횡단 문제(절 간 수치·용어 충돌, 법령 시점 상충,
    챕터 간 통계 중복)를 사람이 편집기에서 판단할 수 있게 저장한다.
    재실행 시 프로젝트 단위 전량 교체(delete-all → insert).
    """

    __tablename__ = "verify_findings"

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
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # critical | warning
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    section_ref: Mapped[str | None] = mapped_column(String(20))  # 예: "2.1"
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_verify_findings_project", "project_id"),)
