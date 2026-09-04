from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class GlossaryTerm(Base):
    """정본 용어집 — 사람이 확정한 (원어, 한글 표기) 쌍. 채굴 용어표보다 우선한다.

    채굴(indexing/terms)은 자료가 말한 대로 적립할 뿐이라 자료끼리 표기가 갈리면
    상충으로 남는다(2026-08-28 v7 실측). 검토 화면에서 사람이 경고를 처리하는 순간
    표기를 확정해 여기 승격하면, 주입(generation/term_rules)은 이 표기를 강제하고
    검사(qa/term_notation)는 이것을 잣대로 쓴다 — 상충이 근원에서 소멸한다.

    2층 범위(2026-09-04 사용자 결정): project_id가 NULL이면 회사 공유(전 프로젝트),
    값이 있으면 그 프로젝트 전용 덮어쓰기 — 같은 term_key면 프로젝트 행이 이긴다.
    에퀴닉스류 일반 용어는 전사 통일, 주제 따라 표기가 갈리는 도메인 용어는 예외 지정.

    미확정 후보는 이 테이블에 없다 — 조립 때마다 경고로 재계산되므로 잃어버릴 수
    없고, 확정된 것만 여기 남아 정본이 된다.
    """

    __tablename__ = "glossary_terms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # NULL = 회사 공유. 프로젝트가 지워지면 그 덮어쓰기도 함께 지운다.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    # 정규화 원어 키 — en 소문자(공백 정규화), en이 없으면 abbr. 조회·유일성의 축.
    term_key: Mapped[str] = mapped_column(String(160), nullable=False)
    en: Mapped[str | None] = mapped_column(String(160))
    abbr: Mapped[str | None] = mapped_column(String(40))
    ko: Mapped[str] = mapped_column(String(120), nullable=False)
    # 선택 — 확정 시 함께 봉인할 정의(자료 원문 문장). 주입에 그대로 실린다.
    definition: Mapped[str | None] = mapped_column(Text)
    # 표기의 출신: document(자료 병기) | convention(관용 후보) | manual(직접 입력)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="manual")
    # 승격 근거 메모(출처 자료명·발견 문맥 등) — 나중에 "왜 이 표기였지"를 되짚는 용도.
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_glossary_terms_project", "project_id"),
        # 유일성은 층별로 따로 — NULL은 유니크 제약에서 서로 다른 값이라 부분 색인 2개.
        Index(
            "uq_glossary_terms_org_key",
            "term_key",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
        Index(
            "uq_glossary_terms_project_key",
            "project_id",
            "term_key",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
        ),
    )
