from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class UserPrompt(Base):
    """개인 프롬프트 레이어 — 사용자별 커스텀 에이전트/작성 규칙.

    시스템 카탈로그(src/prompts 파일)를 단일 진실로 두고, 이 테이블은 그 위에 얹는
    개인 오버레이다. 해석은 '개인 → 시스템 폴백'(src/services/prompts/personal.py):
    - kind='agent': 분석 에이전트 페르소나. base_ref가 있으면 그 시스템 에이전트를
      덮어쓰고(프롬프트만 교체), 없으면 새 개인 에이전트.
    - kind='rule' : 작성 규칙(components/*.md에 대응). base_ref=조각 이름이면 그 조각을
      덮어쓰고, 없으면 새 개인 규칙.
    """

    __tablename__ = "user_prompts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 덮어쓸 시스템 항목 참조(에이전트 id/name 또는 조각 이름). None이면 새 개인 항목.
    base_ref: Mapped[str | None] = mapped_column(String(100))
    cat: Mapped[str | None] = mapped_column(String(100))
    # 구조화 설정(opaque) — 에이전트: {"volume": "short|normal|long", "queries": [...]}.
    # 프롬프트 텍스트로 표현할 수 없는 값(분량 목표)이 여기 들어간다.
    spec: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(String(500))
    # 공개 여부 — 켜면 전 계정의 에이전트 선택 목록에 뜬다(본인 토글, 승인 절차 없음:
    # 2026-08-19 사용자 결정). kind='agent'에만 의미가 있다 — 작성 규칙은 프로젝트에서
    # id로 골라 붙이는 소유자 스코프 계약이라(_validate_rules_config) 공개 대상이 아니다.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("kind IN ('agent', 'rule')", name="user_prompts_kind_check"),
        UniqueConstraint("owner_id", "kind", "name", name="uq_user_prompts_owner_kind_name"),
    )
