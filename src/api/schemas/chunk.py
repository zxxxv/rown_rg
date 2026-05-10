from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ChunkTrack = Literal["content", "style"]


class ChunkBase(BaseModel):
    track: ChunkTrack = Field(..., description="트랙 (content: 내용, style: 문체)")
    content: str = Field(..., min_length=1, description="청크 텍스트")


class ChunkCreate(ChunkBase):
    project_id: UUID | None = Field(None, description="프로젝트 ID (style 트랙은 NULL 가능)")
    source_id: UUID | None = Field(None, description="소스 ID")
    embedding: list[float] | None = Field(None, description="1024차원 벡터")
    metadata: dict[str, Any] = Field(default_factory=dict, description="추가 메타데이터")
    chunk_index: int | None = Field(None, ge=0, description="청크 순서")


class ChunkUpdate(BaseModel):
    content: str | None = Field(None, min_length=1)
    embedding: list[float] | None = None
    metadata: dict[str, Any] | None = None


class ChunkRead(ChunkBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    source_id: UUID | None
    metadata: dict[str, Any]
    chunk_index: int | None
    created_at: datetime
