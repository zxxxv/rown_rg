from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AssertionType = Literal["numeric", "qualitative", "source", "decision"]


class ConsistencyGraphNodeBase(BaseModel):
    assertion_text: str = Field(..., min_length=1, description="단언 텍스트")
    assertion_type: AssertionType = Field(..., description="단언 유형")
    subject: str = Field(..., min_length=1, max_length=255, description="주제")
    value: str | None = Field(None, max_length=255, description="값")


class ConsistencyGraphNodeCreate(ConsistencyGraphNodeBase):
    project_id: UUID = Field(..., description="프로젝트 ID")
    source_reference: str | None = Field(None, description="자료 출처")
    is_user_decision: bool = Field(False, description="사용자 결정 노드 여부")
    asserted_in: list[str] = Field(default_factory=list, description="사용된 섹션 목록")
    level: str | None = Field(None, description="등록 레벨")


class ConsistencyGraphNodeUpdate(BaseModel):
    assertion_text: str | None = Field(None, min_length=1)
    assertion_type: AssertionType | None = None
    subject: str | None = Field(None, min_length=1, max_length=255)
    value: str | None = None
    source_reference: str | None = None
    is_user_decision: bool | None = None
    asserted_in: list[str] | None = None
    level: str | None = None


class ConsistencyGraphNodeRead(ConsistencyGraphNodeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    source_reference: str | None
    is_user_decision: bool
    asserted_in: list[str]
    level: str | None
    created_at: datetime
    updated_at: datetime
