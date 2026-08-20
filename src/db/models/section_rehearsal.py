from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class SectionRehearsal(Base):
    """절별 검색 리허설 결과 — 작성이 같은 근거를 그대로 재사용하는 캐시.

    프로젝트당 절당 1행(최신만 유지). index_version이 프로젝트 현재 값과 다르면
    낡은 것이다(청크가 변했으므로 리허설↔작성 동근거 보장이 깨진다) — 소비자는
    버전이 맞을 때만 쓴다. section_id는 config plan 정본의 section_id와 같은 값이라
    목차가 갈리면 자연히 조회되지 않는다(FK 없음).
    """

    __tablename__ = "section_rehearsals"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 계획 지문(services/retrieval/rehearsal.plan_fingerprint) — 절 id가 안정화되면서
    # (2026-08-21) "목차가 갈리면 id가 바뀌어 자연히 미스"라는 가정이 깨졌다. 제목·
    # 방향·핵심 포인트·검색 질의가 바뀐 절은 이 지문이 갈려 재검색한다. ''=옛 행.
    plan_hash: Mapped[str] = mapped_column(String(40), server_default="", nullable=False)
    # ok(충분) | hyde(HyDE 재검색 수행) | empty(공백) | live(리허설 없이 작성 시점 기록)
    band: Mapped[str] = mapped_column(String(10), nullable=False)
    floor_passed: Mapped[int] = mapped_column(Integer, nullable=False)
    needed: Mapped[int] = mapped_column(Integer, nullable=False)
    hyde_used: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # [{"id": "<chunk uuid>", "score": 0.87}, ...] — 순위 순. 본문은 chunks 테이블이 진실.
    chunks: Mapped[list] = mapped_column(  # type: ignore[assignment]
        JSONB, server_default="[]", nullable=False
    )
    # {"constructive": bool, "raptor_gap": bool} — 공백 경고의 세부 분류.
    warnings: Mapped[dict] = mapped_column(  # type: ignore[assignment]
        JSONB, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
