from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.core.types import ProjectStage

# status는 ProjectStage(core)를 단일 진실로 삼아 파생한다. 별도 Literal로 중복 정의하지 않는다.
ProjectStatus = ProjectStage
DepthMode = Literal["outline_only", "standard", "full_report", "deep_dive"]


class ProjectBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="프로젝트 제목")
    topic: str = Field(..., min_length=1, description="보고서 주제")


class ConfigUpdateRequest(BaseModel):
    """진행 중 옵션 변경 — config 전체 교체(프론트가 폼 전체 값을 보낸다)."""

    config: dict[str, Any]


class PresetRead(BaseModel):
    """생성 화면 프리셋 선택용 카탈로그 항목. 생성 시 preset에 id 또는 name을 넣는다."""

    id: str
    name: str
    desc: str
    n_chapters: int
    n_sections: int


class ProjectCreate(ProjectBase):
    preset: str | None = Field(
        None,
        max_length=100,
        description="보고서 유형 프리셋 키 (예: 예비타당성조사). None=자유 주제",
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
