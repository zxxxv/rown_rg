from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ProjectStatus = Literal[
    "created", "researching", "indexing", "writing", "reviewing", "completed", "archived"
]
DepthMode = Literal["outline_only", "standard", "full_report", "deep_dive"]


class ProjectBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="프로젝트 제목")
    topic: str = Field(..., min_length=1, description="보고서 주제")


class ProjectCreate(ProjectBase):
    preset: str | None = Field(
        None, max_length=100, description="프리셋 (예: preliminary_feasibility)"
    )
    config: dict[str, Any] = Field(default_factory=dict, description="모듈식 옵션")
    depth_mode: DepthMode = Field("full_report", description="보고서 깊이")


class ProjectUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    topic: str | None = Field(None, min_length=1)
    preset: str | None = None
    config: dict[str, Any] | None = None
    status: ProjectStatus | None = None
    depth_mode: DepthMode | None = None


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    preset: str | None
    config: dict[str, Any]
    status: ProjectStatus
    depth_mode: DepthMode
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
