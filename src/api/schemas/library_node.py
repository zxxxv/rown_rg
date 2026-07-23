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


# ─── 트리 응답(프론트 계약) ──────────────────────────────────────────────
# web/src/api/types.ts LibraryNodeSchema와 1:1 — 파일은 file_meta, 폴더는 children.
# page_count·project_id는 값이 없으면 응답에서 생략해야 하므로(zod .optional())
# 라우터에서 response_model_exclude_none=True로 내보낸다.


class LibraryFileMeta(BaseModel):
    size_bytes: int
    registered_at: datetime
    registered_by: str
    source_kind: str
    page_count: int | None = None
    visible_to_roles: list[str]
    project_id: str | None = None


class LibraryTreeFile(BaseModel):
    # 프론트 계약(LibraryNodeSchema)은 id를 string으로 본다. 실노드는 UUID를 str로,
    # 합성 노드(프로젝트 폴더 등)는 "proj-..." 같은 비-UUID 문자열을 쓴다.
    id: str
    name: str
    type: Literal["file"] = "file"
    file_meta: LibraryFileMeta


class LibraryTreeFolder(BaseModel):
    id: str
    name: str
    type: Literal["folder"] = "folder"
    children: list[LibraryTreeFolder | LibraryTreeFile] = Field(default_factory=list)


class LibraryTreeResponse(BaseModel):
    tree: list[LibraryTreeFolder | LibraryTreeFile]


# 자기참조(children) 해석 확정 — from __future__ annotations 지연 평가 대응
LibraryTreeFolder.model_rebuild()
LibraryTreeResponse.model_rebuild()


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: UUID | None = None


class VisibilityUpdateRequest(BaseModel):
    visible_to_roles: list[str] = Field(..., description="열람 허용 역할(빈 목록=전체 공개)")


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
