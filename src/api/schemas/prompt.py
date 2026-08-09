"""개인/시스템 프롬프트 API 스키마 — 라이브러리 '프롬프트' 폴더 편집·조회 계약.

프론트 계약(web/src/api/prompts.ts)과 1:1. kind='agent'(분석 에이전트)/'rule'(작성 규칙).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PromptKind = Literal["agent", "rule"]


class PromptSpec(BaseModel):
    """프롬프트 텍스트로 표현할 수 없는 구조화 설정(에이전트 전용).

    volume은 목표 분량 3단(short/normal/long) — 없으면 '보통'으로 동작한다.
    """

    volume: Literal["short", "normal", "long"] | None = None
    queries: list[str] = Field(default_factory=list, max_length=10)


class PersonalPromptCreate(BaseModel):
    kind: PromptKind = Field(..., description="agent(분석 에이전트) 또는 rule(작성 규칙)")
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, description="프롬프트/규칙 본문")
    # 덮어쓸 시스템 항목(에이전트 id/name 또는 조각 이름). None이면 새 개인 항목.
    base_ref: str | None = Field(None, max_length=100)
    cat: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    spec: PromptSpec = Field(default_factory=PromptSpec)


class PersonalPromptUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    cat: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    spec: PromptSpec | None = None


class PersonalPromptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: PromptKind
    name: str
    content: str
    base_ref: str | None
    cat: str | None
    description: str | None
    spec: dict = Field(default_factory=dict)
    updated_at: datetime


class SystemPromptRead(BaseModel):
    """시스템 카탈로그(읽기전용) 1건 — 에이전트 또는 작성 규칙."""

    ref: str = Field(..., description="에이전트 id 또는 조각 이름")
    kind: PromptKind
    name: str
    content: str
    cat: str | None = None
    description: str | None = None
