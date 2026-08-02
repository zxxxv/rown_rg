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


class PresetSectionRead(BaseModel):
    """프리셋 목차의 절 1개 — 생성 화면 목차 편집기의 초기값."""

    title: str
    direction: str = ""
    key_points: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)


class PresetChapterRead(BaseModel):
    title: str
    sections: list[PresetSectionRead]


class PresetDetailRead(BaseModel):
    """프리셋 전체 골격 — 사용자가 클릭해 들어가 목차·에이전트를 편집하는 출발점."""

    id: str
    name: str
    desc: str
    domain_context: str = ""
    chapters: list[PresetChapterRead]


class AnalystRead(BaseModel):
    """분석 에이전트 카탈로그 항목 — 섹션별 배정 UI용 (프롬프트 본문은 노출 안 함)."""

    id: str
    name: str
    cat: str
    desc: str
    pages: str | None = None  # volume_target 분량 안내 (예: "10~15")


class OutlineSectionIn(BaseModel):
    """사용자 확정 목차의 절 1개 (config.outline). analysts는 카탈로그 이름 참조."""

    title: str = Field(..., min_length=1, max_length=255)
    direction: str = ""
    key_points: list[str] = Field(default_factory=list)
    analysts: list[str] = Field(default_factory=list)


class OutlineChapterIn(BaseModel):
    title: str = Field("", max_length=255)
    sections: list[OutlineSectionIn] = Field(default_factory=list)


class OutlineIn(BaseModel):
    """생성 화면에서 확정한 목차 — 있으면 planner LLM을 생략하고 그대로 실행된다."""

    chapters: list[OutlineChapterIn]


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


class VerifyFindingRead(BaseModel):
    """PM 검증 경고 항목 — assemble 직후 pm_verify가 저장한 문서 횡단 일관성 경고."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_number: int
    severity: str  # critical | warning
    category: str
    section_ref: str | None = None
    detail: str
    created_at: datetime


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    preset: str | None
    config: dict[str, Any]
    status: ProjectStatus
    depth_mode: DepthMode
    owner_id: UUID
    # 표시용 소유자 이름 — 라우터가 owner를 eager-load한 경우에만 채워진다
    owner_name: str | None = None
    created_at: datetime
    updated_at: datetime
