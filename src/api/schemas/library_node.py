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


class WritableTarget(BaseModel):
    """이 폴더가 업로드·폴더생성 대상이 될 때 새 노드에 적용할 컨텍스트.

    parent_id=None은 최상위(개인/회사 공유 루트 바로 아래). scope는 개인/회사 구분.
    writable이 None인 폴더는 쓰기 불가(가상 컨테이너: 프로젝트/프롬프트 등).
    """

    parent_id: str | None = None
    scope: Literal["personal", "company"]


class PromptRef(BaseModel):
    """프롬프트 파일 노드 마커 — 이 노드는 자료 파일이 아니라 프롬프트다.

    프론트는 이 마커가 있으면 상세 패널에서 프롬프트 에디터/뷰어를 연다.
    - scope=personal: ref=user_prompt id(agent·rule) 또는 user_preset id(preset),
      editable=True → PATCH /prompts/personal/{ref} · PUT /presets/personal/{ref}
    - scope=system  : ref=에이전트 id·조각 이름·프리셋 키, editable=False (읽기전용)
    - scope=shared  : 남이 공개한 것. 읽기 전용이되 '내 것으로 가져오기'가 가능하다 —
      남의 자산을 라이브러리에서 고칠 수 있으면 공유가 아니라 공용 편집이 된다.
    """

    scope: Literal["personal", "system", "shared"]
    kind: Literal["agent", "rule", "preset"]
    ref: str
    editable: bool = False
    # shared일 때 누구 것인지. 같은 이름이 둘일 때 사람이 가려 고르는 단서다.
    owner_name: str | None = None
    # 가져오기가 가능한가(shared 전용). 프론트가 '내 것으로 가져오기' 버튼을 띄운다.
    importable: bool = False


class LibraryTreeFile(BaseModel):
    # 프론트 계약(LibraryNodeSchema)은 id를 string으로 본다. 실노드는 UUID를 str로,
    # 합성 노드(프로젝트 폴더 등)는 "proj-..." 같은 비-UUID 문자열을 쓴다.
    id: str
    name: str
    type: Literal["file"] = "file"
    file_meta: LibraryFileMeta
    # 가상 파일(프로젝트 소스·완성본)은 삭제/권한변경 대상이 아니다.
    virtual: bool = False
    # 가상 파일의 다운로드 경로(API base 기준 상대경로 또는 절대 URL). 실파일은 None.
    download_url: str | None = None
    # 수집 본문(content_md)이 있는 자료의 인라인 뷰어 경로(API base 상대). 클릭 시
    # 라이브러리 안에서 원문을 보여준다. 본문 없는 자료는 None. (AI 수집 자료 전용)
    content_url: str | None = None
    # 프롬프트 노드면 마커(에디터/뷰어를 여는 신호). 일반 파일은 None.
    prompt: PromptRef | None = None


class LibraryTreeFolder(BaseModel):
    id: str
    name: str
    type: Literal["folder"] = "folder"
    children: list[LibraryTreeFolder | LibraryTreeFile] = Field(default_factory=list)
    # 가상 컨테이너(개인 루트·프로젝트·프롬프트 등)는 이름변경/삭제 대상이 아니다.
    virtual: bool = False
    # 업로드·폴더생성 대상이면 컨텍스트, 아니면 None(쓰기 불가).
    writable: WritableTarget | None = None


class LibraryTreeResponse(BaseModel):
    tree: list[LibraryTreeFolder | LibraryTreeFile]


# 자기참조(children) 해석 확정 — from __future__ annotations 지연 평가 대응
LibraryTreeFolder.model_rebuild()
LibraryTreeResponse.model_rebuild()


class SourceContentResponse(BaseModel):
    """AI 수집 자료(웹 소스)의 수집 원문 — 라이브러리 인라인 뷰어용.

    원문은 project_sources.metadata_[content_md]에 수집 시점 저장된다(web.py stage()).
    """

    title: str | None = None
    url: str | None = None
    reliability: str | None = None
    content_md: str
    char_count: int
    byte_count: int


class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: UUID | None = None
    # 최상위 생성 시에만 의미(개인=True/회사 공유=False). 상위 폴더가 있으면 상위에서 상속.
    is_personal: bool = False


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
