"""개인/시스템 프롬프트 API 스키마 — 라이브러리 '프롬프트' 폴더 편집·조회 계약.

프론트 계약(web/src/api/prompts.ts)과 1:1. kind='agent'(분석 에이전트)/'rule'(작성 규칙).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

PromptKind = Literal["agent", "rule"]


class PromptSpec(BaseModel):
    """프롬프트 텍스트로 표현할 수 없는 구조화 설정(에이전트 전용).

    목표 분량은 min_chars~max_chars(문자)로 직접 받는다. 둘 다 비면 '지정 없음'이라
    시스템 에이전트를 덮어쓴 경우 원본 분량을 그대로 승계한다. volume은 3단 버튼
    시절의 레거시 값(예전에 저장된 것만 읽는다).
    """

    min_chars: int | None = Field(None, ge=1000, le=60000)
    max_chars: int | None = Field(None, ge=1000, le=60000)
    volume: Literal["short", "normal", "long"] | None = None
    queries: list[str] = Field(default_factory=list, max_length=10)
    # 폼의 칸 값(임무·분석 방법론·핵심 산출물·구성 지침). 주면 서버가 프롬프트를
    # 조합해 content에 넣는다 — 조각은 재편집용으로 여기 남는다.
    sections: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_range(self) -> PromptSpec:
        lo, hi = self.min_chars, self.max_chars
        if (lo is None) != (hi is None):
            raise ValueError("목표 분량은 최소·최대를 함께 지정해야 합니다")
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError("목표 분량의 최소는 최대보다 작아야 합니다")
        return self


class PersonalPromptCreate(BaseModel):
    kind: PromptKind = Field(..., description="agent(분석 에이전트) 또는 rule(작성 규칙)")
    name: str = Field(..., min_length=1, max_length=255)
    # 에이전트는 칸(spec.sections)만 채워도 된다 — 서버가 본문을 조합한다(_check_body).
    # min_length로 강제하면 칸 입력 흐름이 조합 로직에 닿기 전에 잘린다.
    content: str = Field("", description="프롬프트/규칙 본문")
    # 덮어쓸 시스템 항목(에이전트 id/name 또는 조각 이름). None이면 새 개인 항목.
    base_ref: str | None = Field(None, max_length=100)
    cat: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    spec: PromptSpec = Field(default_factory=PromptSpec)

    @model_validator(mode="after")
    def _check_body(self) -> PersonalPromptCreate:
        if self.content.strip():
            return self
        if self.kind == "agent" and any(v.strip() for v in self.spec.sections.values()):
            return self
        if self.kind == "agent":
            raise ValueError("프롬프트 본문 또는 칸(임무·분석 방법론·핵심 산출물)을 채워주세요")
        raise ValueError("규칙 본문을 입력해주세요")


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
    """시스템 카탈로그(읽기전용) 1건 — 에이전트 또는 작성 규칙.

    sections는 '복제해서 시작'이 폼의 칸을 채우도록 서버가 분해해 준 값이다
    (프론트에서 다시 파싱하면 구현이 둘로 갈린다).
    """

    ref: str = Field(..., description="에이전트 id 또는 조각 이름")
    kind: PromptKind
    name: str
    content: str
    cat: str | None = None
    description: str | None = None
    sections: dict[str, str] = Field(default_factory=dict)
    # 이 에이전트의 목표 분량(자) — 복제 시작 시 폼의 숫자 칸을 채운다.
    min_chars: int | None = None
    max_chars: int | None = None


class PromptPreviewRequest(BaseModel):
    """이 조합으로 작성하면 모델에 무엇이 가는지 — 절 하나 기준."""

    analysts: list[str] = Field(default_factory=list, max_length=5)
    rules: list[UUID] = Field(default_factory=list, max_length=10)
    title: str = ""
    direction: str = ""
    key_points: list[str] = Field(default_factory=list, max_length=20)


class PromptPreviewBlock(BaseModel):
    label: str
    chars: int


class PromptPreviewResponse(BaseModel):
    """조립 결과. blocks는 무엇이 얼마나 차지하는지 한눈에 보기 위한 내역이다."""

    system: str
    guidance: str
    blocks: list[PromptPreviewBlock] = Field(default_factory=list)
    min_chars: int | None = None
    max_chars: int | None = None
    n_parts: int = 1
    unknown_analysts: list[str] = Field(default_factory=list)
