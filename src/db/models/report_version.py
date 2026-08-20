from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ReportVersion(Base):
    """보고서 버전 스냅샷 — append-only(2026-08-21 버전 관리 설계).

    sections는 절당 1행 덮어쓰기 구조라 이전 내용이 어디에도 안 남았다. 이 테이블이
    보존 지점이다: 조립 완료(assemble)와 재개(reopen) 직전에 절 전량을 JSONB로 얼린다.
    diff는 저장하지 않는다 — 조회 시 두 스냅샷을 절 id로 맞춰 계산한다(전량 저장이
    버전당 수백 KB 수준이라 델타 저장의 복잡도가 이득을 넘는다).

    행은 지우지 않는다(review_points와 같은 append-only 규약). 프로젝트 삭제 시에만
    CASCADE로 함께 사라진다.
    """

    __tablename__ = "report_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 프로젝트 안에서 1부터 증가 — 사람이 부르는 이름("v2")이 곧 이 값.
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # 스냅샷 사유: assemble(조립 완성) | reopen(재개 직전 보존) | manual(사용자 버튼)
    reason: Mapped[str] = mapped_column(String(10), nullable=False)
    # [{"section_id","chapter_number","section_number","chapter_title","title",
    #   "content","source_ids"}] — 절 순서 그대로. 본문 정본은 이 배열이고,
    # HWPX는 요청 시 이 내용으로 재렌더한다(파일 보관 안 함).
    sections: Mapped[list] = mapped_column(  # type: ignore[assignment]
        JSONB, server_default="[]", nullable=False
    )
    # 내용 지문(sha1) — 같은 내용의 연속 스냅샷을 걸러낸다(assemble 직후 reopen 등).
    content_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("project_id", "version_no", name="uq_report_version_no"),)
