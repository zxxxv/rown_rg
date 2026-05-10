from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal["folder", "file"]


class LibraryNodeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="노드 이름")
    type: NodeType = Field(..., description="노드 유형")


class LibraryNodeCreate(LibraryNodeBase):
    parent_id: UUID | None = Field(None, description="상위 폴더 ID")
    file_path: str | None = Field(None, description="Object Storage 경로")
    file_size: int | None = Field(None, ge=0, description="파일 크기 (bytes)")
    mime_type: str | None = Field(None, max_length=100, description="MIME 타입")
    metadata: dict[str, Any] = Field(default_factory=dict, description="추가 메타데이터")
    visible_to_users: list[UUID] = Field(default_factory=list, description="열람 허용 사용자 ID")
    visible_to_roles: list[str] = Field(default_factory=list, description="열람 허용 역할")


class LibraryNodeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    parent_id: UUID | None = None
    file_path: str | None = None
    metadata: dict[str, Any] | None = None
    visible_to_users: list[UUID] | None = None
    visible_to_roles: list[str] | None = None


class LibraryNodeRead(LibraryNodeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID | None
    file_path: str | None
    file_size: int | None
    mime_type: str | None
    metadata: dict[str, Any]
    created_by: UUID | None
    visible_to_users: list[UUID]
    visible_to_roles: list[str]
    created_at: datetime
    updated_at: datetime
