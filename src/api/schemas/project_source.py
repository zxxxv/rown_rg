from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.types import SourceType

Reliability = Literal["high", "medium", "low"]


class ProjectSourceBase(BaseModel):
    source_type: SourceType = Field(..., description="자료 유형")
    title: str | None = Field(None, max_length=500, description="자료 제목")


class ProjectSourceCreate(ProjectSourceBase):
    project_id: UUID = Field(..., description="프로젝트 ID")
    library_node_id: UUID | None = Field(None, description="라이브러리 노드 ID")
    upload_path: str | None = Field(None, description="업로드 경로")
    url: str | None = Field(None, description="웹 자료 URL")
    reliability: Reliability | None = Field(None, description="신뢰도")
    is_included: bool = Field(True, description="포함 여부")
    metadata: dict[str, Any] = Field(default_factory=dict, description="추가 메타데이터")


class ProjectSourceUpdate(BaseModel):
    title: str | None = Field(None, max_length=500)
    reliability: Reliability | None = None
    is_included: bool | None = None
    metadata: dict[str, Any] | None = None


class ProjectSourceRead(ProjectSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    library_node_id: UUID | None
    upload_path: str | None
    url: str | None
    reliability: Reliability | None
    is_included: bool
    metadata: dict[str, Any]
    created_at: datetime
