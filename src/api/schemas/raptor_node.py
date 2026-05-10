from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RaptorNodeBase(BaseModel):
    level: int = Field(..., ge=0, description="계층 수준 (0=leaf)")
    summary: str | None = Field(None, description="요약 텍스트 (level > 0)")


class RaptorNodeCreate(RaptorNodeBase):
    project_id: UUID = Field(..., description="프로젝트 ID")
    parent_id: UUID | None = Field(None, description="상위 노드 ID")
    embedding: list[float] = Field(..., description="1024차원 벡터")
    chunk_ids: list[UUID] = Field(default_factory=list, description="포함된 청크 ID")
    metadata: dict[str, Any] = Field(default_factory=dict, description="추가 메타데이터")


class RaptorNodeUpdate(BaseModel):
    summary: str | None = None
    embedding: list[float] | None = None
    chunk_ids: list[UUID] | None = None
    metadata: dict[str, Any] | None = None


class RaptorNodeRead(RaptorNodeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    parent_id: UUID | None
    chunk_ids: list[UUID]
    metadata: dict[str, Any]
    created_at: datetime
