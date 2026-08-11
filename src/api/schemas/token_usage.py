from __future__ import annotations

from datetime import date, datetime
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


class TokenUsageDailyPoint(BaseModel):
    """일별 집계 — 마이페이지 사용량 차트용"""

    date: date
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class TokenUsageByModel(BaseModel):
    """모델별 집계 — 마이페이지 사용량 표용"""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    request_count: int


class MyTokenUsageResponse(BaseModel):
    """현재 사용자의 기간(기본: 이번 달) 토큰 사용량 요약."""

    period_start: date
    period_end: date
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal
    request_count: int
    # 실제로 집행되는 한도는 비용(달러)이다 - 토큰 수로 막지 않는다(clients/llm/quota_gate).
    # 화면이 "토큰 x / y" 같은 없는 상한을 지어내지 않도록 여기서 진짜 한도를 내려보낸다.
    cost_limit_usd: Decimal | None = None
    daily: list[TokenUsageDailyPoint]
    by_model: list[TokenUsageByModel]
