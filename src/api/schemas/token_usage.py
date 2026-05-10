from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TokenMode = Literal["live", "record", "replay"]


class TokenUsageBase(BaseModel):
    model: str = Field(..., max_length=50, description="모델 이름")
    operation: str = Field(..., max_length=50, description="작업 유형")
    input_tokens: int = Field(..., ge=0, description="입력 토큰 수")
    output_tokens: int = Field(..., ge=0, description="출력 토큰 수")


class TokenUsageCreate(TokenUsageBase):
    user_id: UUID | None = Field(None, description="사용자 ID")
    project_id: UUID | None = Field(None, description="프로젝트 ID")
    cached_input_tokens: int = Field(0, ge=0, description="캐시된 입력 토큰 수")
    cost_usd: Decimal = Field(..., ge=0, description="비용 (USD)")
    mode: TokenMode = Field(..., description="실행 모드")


class TokenUsageUpdate(BaseModel):
    pass


class TokenUsageRead(TokenUsageBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    project_id: UUID | None
    cached_input_tokens: int
    mode: TokenMode
    created_at: datetime


class TokenUsageReadAdmin(TokenUsageRead):
    """Admin 전용 — cost_usd 포함"""

    cost_usd: Decimal
