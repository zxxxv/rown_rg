"""자료 라이브러리 — 폴더 트리 + 파일 저장.

프론트 계약(web/src/api/library.ts·types.ts LibraryNodeSchema)에 맞춘다:
GET tree, POST folders, POST files(multipart), DELETE nodes/{id},
GET files/{id}/download, PATCH nodes/{id}/visibility.

권한 규칙:
- 조회·다운로드: 로그인 사용자 전체. 단 파일의 visible_to_roles/visible_to_users가
  비어있지 않으면 해당 역할/사용자와 관리자만 볼 수 있다(폴더는 항상 보임).
- 폴더 생성·업로드: worker 이상.
- 삭제: 관리자 또는 생성자 본인.
- 권한(visible_to_roles) 변경: 관리자만.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.dependencies.permissions import ADMINS, require_role
from src.api.schemas.library_node import (
    FolderCreateRequest,
    LibraryFileMeta,
    LibraryTreeFile,
    LibraryTreeFolder,
    LibraryTreeResponse,
    PromptRef,
    SourceContentResponse,
    VisibilityUpdateRequest,
    WritableTarget,
)
from src.core.config import settings
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.types import Role
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from src.prompts import list_analysts, list_components
from src.services.prompts import list_personal

router = APIRouter(prefix="/library", tags=["library"])

WRITERS: tuple[Role, ...] = (Role.SUPER_ADMIN, Role.ADMIN, Role.WORKER)
ALL_ROLES: list[str] = [r.value for r in Role]
VALID_SOURCE_KINDS = {"gov", "academic", "media", "library", "upload", "web_search"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _is_admin(user: User) -> bool:
    return user.role in (Role.SUPER_ADMIN.value, Role.ADMIN.value)


def _can_view(node: LibraryNode, user: User) -> bool:
    # 개인 노드는 소유자 본인만 — 관리자도 타인의 개인 자료는 못 본다("개인=나만").
    if node.is_personal:
        return node.created_by == user.id
    if _is_admin(user):
        return True
    if not node.visible_to_roles and not node.visible_to_users:
        return True
    return user.role in node.visible_to_roles or user.id in node.visible_to_users


def _file_meta(node: LibraryNode, registered_by: str) -> LibraryFileMeta:
    kind = node.metadata_.get("source_kind", "upload")
    if kind not in VALID_SOURCE_KINDS:
        kind = "upload"
    return LibraryFileMeta(
        size_bytes=node.file_size or 0,
        registered_at=node.created_at,
        registered_by=registered_by,
        source_kind=kind,
        page_count=node.metadata_.get("page_count"),
        visible_to_roles=list(node.visible_to_roles) or ALL_ROLES,
        project_id=node.metadata_.get("project_id"),
    )


def _creator_name(node: LibraryNode) -> str:
    # creator는 selectinload로 로드된 경우에만 접근(lazy="raise" 회피)
    creator = node.__dict__.get("creator")
    return creator.name if creator is not None else "알 수 없음"


async def _get_node(session: AsyncSession, node_id: UUID) -> LibraryNode:
    node = await session.get(LibraryNode, node_id, options=[selectinload(LibraryNode.creator)])
    if node is None:
        raise NotFoundError(message="노드를 찾을 수 없습니다", code="NODE_NOT_FOUND")
    return node


async def _get_parent_folder(session: AsyncSession, parent_id: UUID | None) -> LibraryNode | None:
    if parent_id is None:
        return None
    parent = await session.get(LibraryNode, parent_id)
    if parent is None:
        raise NotFoundError(message="상위 폴더를 찾을 수 없습니다", code="PARENT_NOT_FOUND")
    if parent.type != "folder":
        raise ValidationError(message="상위 노드가 폴더가 아닙니다", code="PARENT_NOT_FOLDER")
    return parent


# 프로젝트 소스 타입(project_sources.source_type) → (id 접미사, 가상 하위 폴더 라벨).
_SOURCE_GROUPS: list[tuple[str, str, str]] = [
    ("web_search", "ai", "AI 수집 자료"),
    ("upload", "up", "사용자 업로드"),
    ("library", "lib", "라이브러리 참조"),
]
_REGISTERED_BY = {"web_search": "AI 수집", "library": "라이브러리 참조"}


def _source_file(src: ProjectSource, pid: UUID, owner_name: str) -> LibraryTreeFile:
    """project_sources 1건 → 가상 파일 노드(읽기전용). 원본 복사 없이 참조만."""
    name = src.title or src.url or f"{src.source_type} 자료"
    # AI 수집 자료의 원문(content_md)은 수집 시점 metadata_에 저장돼 있다(web.py stage()).
    content_md = (src.metadata_ or {}).get("content_md") or ""
    download_url: str | None = None
    content_url: str | None = None
    if src.source_type == "library" and src.library_node_id is not None:
        download_url = f"library/files/{src.library_node_id}/download"
    elif src.source_type == "web_search" and src.url:
        download_url = src.url
    # 수집 본문이 있으면 인라인 뷰어 경로를 준다 — 클릭 시 라이브러리 안에서 원문을 표시.
    if content_md.strip():
        content_url = f"library/sources/{src.id}/content"
    return LibraryTreeFile(
        id=f"ps-{src.id}",
        name=name,
        virtual=True,
        download_url=download_url,
        content_url=content_url,
        file_meta=LibraryFileMeta(
            # 물리 파일이 없으므로 수집 본문의 UTF-8 바이트 길이를 크기로 쓴다(0 하드코딩 폐지).
            size_bytes=len(content_md.encode("utf-8")),
            registered_at=src.created_at,
            registered_by=_REGISTERED_BY.get(src.source_type, owner_name),
            source_kind=src.source_type,
            visible_to_roles=ALL_ROLES,
            project_id=str(pid),
        ),
    )


def _project_folder(
    pid: UUID,
    title: str,
    updated_at: datetime,
    sources: list[ProjectSource],
    owner_name: str,
) -> LibraryTreeFolder:
    """프로젝트 1건 → 가상 폴더(완성본 + AI수집/업로드/참조). project_sources·export 합성."""
    base = f"proj-{pid}"
    children: list[LibraryTreeFolder | LibraryTreeFile] = []

    # 완성본 — export가 렌더된 프로젝트만(결정적 경로 <export_dir>/<id>.hwpx).
    export_path = Path(settings.export_dir) / f"{pid}.hwpx"
    if export_path.is_file():
        children.append(
            LibraryTreeFile(
                id=f"{base}-final",
                name=f"{title}.hwpx",
                virtual=True,
                download_url=f"projects/{pid}/export",
                file_meta=LibraryFileMeta(
                    size_bytes=export_path.stat().st_size,
                    registered_at=updated_at,
                    registered_by=owner_name,
                    source_kind="library",
                    visible_to_roles=ALL_ROLES,
                    project_id=str(pid),
                ),
            )
        )

    for source_type, suffix, label in _SOURCE_GROUPS:
        files: list[LibraryTreeFolder | LibraryTreeFile] = [
            _source_file(s, pid, owner_name) for s in sources if s.source_type == source_type
        ]
        children.append(
            LibraryTreeFolder(id=f"{base}-{suffix}", name=label, virtual=True, children=files)
        )

    return LibraryTreeFolder(id=base, name=title, virtual=True, children=children)


async def _build_projects_folder(session: AsyncSession, current_user: User) -> LibraryTreeFolder:
    """개인 루트의 '프로젝트' 가상 폴더 — 내 프로젝트만(소유자 스코프).

    관리자도 라이브러리에선 자기 프로젝트만 본다(전체 조회는 admin 대시보드/scope=all).
    이로써 라이브러리와 프로젝트 목록(기본 scope=mine)의 가시성이 일치한다.
    """
    projs = (
        await session.execute(
            select(Project.id, Project.title, Project.updated_at)
            .where(Project.owner_id == current_user.id)
            .order_by(Project.created_at.desc())
        )
    ).all()
    empty = LibraryTreeFolder(id="me-projects", name="프로젝트", virtual=True, children=[])
    if not projs:
        return empty

    proj_ids = [p.id for p in projs]
    srcs = (
        (
            await session.execute(
                select(ProjectSource)
                .where(ProjectSource.project_id.in_(proj_ids))
                .order_by(ProjectSource.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    by_proj: dict[UUID, list[ProjectSource]] = defaultdict(list)
    for s in srcs:
        by_proj[s.project_id].append(s)

    empty.children = [
        _project_folder(pid, title, updated_at, by_proj[pid], current_user.name)
        for pid, title, updated_at in projs
    ]
    return empty


# 시스템 프롬프트 노드 표시용 고정 타임스탬프(파일 카탈로그라 등록 시각이 없다).
_PROMPT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _prompt_file(
    node_id: str, name: str, ref: PromptRef, registered_by: str, registered_at: datetime
) -> LibraryTreeFile:
    """프롬프트 파일 노드(가상). 프론트는 prompt 마커로 에디터를 연다(file_meta=표시용 최소값)."""
    return LibraryTreeFile(
        id=node_id,
        name=name,
        virtual=True,
        prompt=ref,
        file_meta=LibraryFileMeta(
            size_bytes=0,
            registered_at=registered_at,
            registered_by=registered_by,
            source_kind="library",
            visible_to_roles=ALL_ROLES,
        ),
    )


async def _build_prompts_folder(session: AsyncSession, current_user: User) -> LibraryTreeFolder:
    """개인 루트의 '프롬프트' 폴더 — 내 에이전트/내 작성 규칙(개인 DB, 편집 가능)."""
    personals = await list_personal(session, current_user.id)

    def node(p: object) -> LibraryTreeFile:
        return _prompt_file(
            f"uprompt-{p.id}",  # type: ignore[attr-defined]
            p.name,  # type: ignore[attr-defined]
            PromptRef(scope="personal", kind=p.kind, ref=str(p.id), editable=True),  # type: ignore[attr-defined]
            current_user.name,
            p.updated_at,  # type: ignore[attr-defined]
        )

    agents: list[LibraryTreeFolder | LibraryTreeFile] = [
        node(p) for p in personals if p.kind == "agent"
    ]
    rules: list[LibraryTreeFolder | LibraryTreeFile] = [
        node(p) for p in personals if p.kind == "rule"
    ]
    return LibraryTreeFolder(
        id="me-prompts",
        name="프롬프트",
        virtual=True,
        children=[
            LibraryTreeFolder(id="me-agents", name="내 에이전트", virtual=True, children=agents),
            LibraryTreeFolder(id="me-rules", name="내 작성 규칙", virtual=True, children=rules),
        ],
    )


def _system_prompts_folder() -> LibraryTreeFolder:
    """회사 공유의 '시스템 프롬프트' 폴더 — 에이전트/작성 규칙(파일 카탈로그, 읽기전용)."""
    agents: list[LibraryTreeFolder | LibraryTreeFile] = [
        _prompt_file(
            f"sysagent-{a.id}",
            a.name,
            PromptRef(scope="system", kind="agent", ref=a.id, editable=False),
            "시스템",
            _PROMPT_EPOCH,
        )
        for a in list_analysts()
    ]
    rules: list[LibraryTreeFolder | LibraryTreeFile] = [
        _prompt_file(
            f"syscomp-{name}",
            name,
            PromptRef(scope="system", kind="rule", ref=name, editable=False),
            "시스템",
            _PROMPT_EPOCH,
        )
        for name in list_components()
    ]
    return LibraryTreeFolder(
        id="sys-prompts",
        name="시스템 프롬프트",
        virtual=True,
        children=[
            LibraryTreeFolder(id="sys-agents", name="에이전트", virtual=True, children=agents),
            LibraryTreeFolder(id="sys-rules", name="작성 규칙", virtual=True, children=rules),
        ],
    )


@router.get("/tree", response_model=LibraryTreeResponse, response_model_exclude_none=True)
async def get_tree(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> LibraryTreeResponse:
    """개인 루트(나만) + 회사 공유(조직 전체) 2탑레벨.

    - 개인 루트: 프로젝트(가상뷰)·프롬프트(Phase 2)·내 자료(개인 업로드) — 전부 소유자 스코프.
    - 회사 공유: 실 library_nodes(is_personal=false), 파일은 역할 가시성 적용.
    가상 노드(프로젝트/완성본/소스 등)는 virtual=True로 표시, 업로드·삭제 대상이 아니다.
    """
    nodes = (
        (
            await session.execute(
                select(LibraryNode)
                .options(selectinload(LibraryNode.creator))
                .order_by(LibraryNode.type.desc(), LibraryNode.name)
            )
        )
        .scalars()
        .all()
    )
    children_map: dict[UUID | None, list[LibraryNode]] = defaultdict(list)
    for n in nodes:
        children_map[n.parent_id].append(n)

    def build(n: LibraryNode) -> LibraryTreeFolder | LibraryTreeFile | None:
        if n.type == "file":
            if not _can_view(n, current_user):
                return None
            return LibraryTreeFile(
                id=str(n.id), name=n.name, file_meta=_file_meta(n, _creator_name(n))
            )
        children = [built for c in children_map[n.id] if (built := build(c)) is not None]
        scope: Literal["personal", "company"] = "personal" if n.is_personal else "company"
        return LibraryTreeFolder(
            id=str(n.id),
            name=n.name,
            children=children,
            writable=WritableTarget(parent_id=str(n.id), scope=scope),
        )

    # 최상위 실 노드를 회사 공유 / 내 개인으로 가른다.
    company_roots = [
        built for n in children_map[None] if not n.is_personal and (built := build(n)) is not None
    ]
    personal_roots = [
        built
        for n in children_map[None]
        if n.is_personal and n.created_by == current_user.id and (built := build(n)) is not None
    ]

    projects_folder = await _build_projects_folder(session, current_user)
    prompts_folder = await _build_prompts_folder(session, current_user)

    me_root = LibraryTreeFolder(
        id="me",
        name=current_user.name,
        virtual=True,
        children=[
            projects_folder,
            prompts_folder,
            LibraryTreeFolder(
                id="me-files",
                name="내 자료",
                virtual=True,
                writable=WritableTarget(parent_id=None, scope="personal"),
                children=personal_roots,
            ),
        ],
    )
    company_root = LibraryTreeFolder(
        id="company",
        name="회사 공유",
        virtual=True,
        writable=WritableTarget(parent_id=None, scope="company"),
        # 실 공유 노드(seed 포함) + 시스템 프롬프트(읽기전용) 카탈로그.
        children=[*company_roots, _system_prompts_folder()],
    )

    return LibraryTreeResponse(tree=[me_root, company_root])


@router.post(
    "/folders",
    response_model=LibraryTreeFolder,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    data: FolderCreateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*WRITERS))],
) -> LibraryTreeFolder:
    parent = await _get_parent_folder(session, data.parent_id)
    # 상위 폴더가 있으면 개인/공유 성격을 상속, 최상위면 요청값(개인 루트=True/회사=False).
    is_personal = parent.is_personal if parent is not None else data.is_personal
    node = LibraryNode(
        name=data.name,
        type="folder",
        parent_id=data.parent_id,
        created_by=current_user.id,
        is_personal=is_personal,
    )
    session.add(node)
    await session.flush()
    await session.refresh(node)
    scope: Literal["personal", "company"] = "personal" if is_personal else "company"
    return LibraryTreeFolder(
        id=str(node.id),
        name=node.name,
        children=[],
        writable=WritableTarget(parent_id=str(node.id), scope=scope),
    )


@router.post(
    "/files",
    response_model=LibraryTreeFile,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*WRITERS))],
    parent_id: Annotated[UUID | None, Form()] = None,
    is_personal: Annotated[bool, Form()] = False,
) -> LibraryTreeFile:
    parent = await _get_parent_folder(session, parent_id)
    # 상위 폴더가 있으면 개인/공유 성격 상속, 최상위면 요청값(내 자료=True/회사 공유=False).
    personal = parent.is_personal if parent is not None else is_personal
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            message=f"파일이 너무 큽니다(최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)",
            code="FILE_TOO_LARGE",
        )
    safe_name = Path(file.filename or "untitled").name

    node = LibraryNode(
        name=safe_name,
        type="file",
        parent_id=parent_id,
        file_size=len(content),
        mime_type=file.content_type,
        metadata_={"source_kind": "upload"},
        created_by=current_user.id,
        is_personal=personal,
    )
    session.add(node)
    await session.flush()

    library_dir = Path(settings.library_dir)
    library_dir.mkdir(parents=True, exist_ok=True)
    dest = library_dir / f"{node.id}_{safe_name}"
    dest.write_bytes(content)
    node.file_path = str(dest)
    await session.flush()
    await session.refresh(node)
    return LibraryTreeFile(
        id=str(node.id), name=node.name, file_meta=_file_meta(node, current_user.name)
    )


@router.get("/files/{node_id}/download")
async def download_file(
    node_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> FileResponse:
    node = await _get_node(session, node_id)
    if node.type != "file" or not _can_view(node, current_user):
        raise NotFoundError(message="노드를 찾을 수 없습니다", code="NODE_NOT_FOUND")
    if not node.file_path or not Path(node.file_path).is_file():
        raise NotFoundError(
            message="파일 본문이 없습니다(메타데이터만 등록된 자료)", code="FILE_BODY_MISSING"
        )
    return FileResponse(
        node.file_path,
        filename=node.name,
        media_type=node.mime_type or "application/octet-stream",
    )


@router.get("/sources/{source_id}/content", response_model=SourceContentResponse)
async def get_source_content(
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SourceContentResponse:
    """AI 수집 자료(웹 소스)의 수집 원문(content_md)을 반환 — 라이브러리 인라인 뷰어용.

    원문은 project_sources.metadata_[content_md]에 수집 시점 저장된다(web.py stage()).
    가시성: 소속 프로젝트 소유자만 — 라이브러리 프로젝트 뷰가 소유자 스코프라 관리자도
    타인 프로젝트를 트리에서 못 보므로, 여기서도 admin 우회 없이 owner로만 한정한다.
    타인 자료·미존재는 존재 은닉을 위해 모두 404로 통일한다.
    """
    row = (
        await session.execute(
            select(ProjectSource, Project.owner_id)
            .join(Project, Project.id == ProjectSource.project_id)
            .where(ProjectSource.id == source_id)
        )
    ).first()
    if row is None:
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    src, owner_id = row
    if owner_id != current_user.id:
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    content_md = (src.metadata_ or {}).get("content_md") or ""
    if not content_md.strip():
        raise NotFoundError(
            message="수집된 본문이 없습니다(메타데이터만 있는 자료)", code="SOURCE_BODY_MISSING"
        )
    return SourceContentResponse(
        title=src.title,
        url=src.url,
        reliability=src.reliability,
        content_md=content_md,
        char_count=len(content_md),
        byte_count=len(content_md.encode("utf-8")),
    )


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_role(*WRITERS))],
) -> None:
    """노드 삭제(폴더는 하위 전체 포함 — DB FK CASCADE). 관리자 또는 생성자만."""
    node = await _get_node(session, node_id)
    if not _is_admin(current_user) and node.created_by != current_user.id:
        raise AuthorizationError(
            message="관리자 또는 생성자만 삭제할 수 있습니다", code="FORBIDDEN"
        )

    # 삭제될 하위 파일 블롭 경로 수집(행 삭제는 CASCADE, 블롭은 직접 정리)
    rows = (
        await session.execute(select(LibraryNode.id, LibraryNode.parent_id, LibraryNode.file_path))
    ).all()
    children_ids: dict[UUID | None, list[UUID]] = defaultdict(list)
    paths: dict[UUID, str | None] = {}
    for rid, parent, fpath in rows:
        children_ids[parent].append(rid)
        paths[rid] = fpath
    doomed: list[UUID] = [node_id]
    stack = [node_id]
    while stack:
        for child in children_ids[stack.pop()]:
            doomed.append(child)
            stack.append(child)

    await session.execute(delete(LibraryNode).where(LibraryNode.id == node_id))
    for rid in doomed:
        fpath = paths.get(rid)
        if fpath:
            Path(fpath).unlink(missing_ok=True)


@router.patch("/nodes/{node_id}/visibility")
async def update_visibility(
    node_id: UUID,
    data: VisibilityUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    _: Annotated[User, Depends(require_role(*ADMINS))],
) -> dict[str, list[str]]:
    invalid = [r for r in data.visible_to_roles if r not in ALL_ROLES]
    if invalid:
        raise ValidationError(message=f"알 수 없는 역할: {', '.join(invalid)}", code="INVALID_ROLE")
    node = await _get_node(session, node_id)
    node.visible_to_roles = data.visible_to_roles
    await session.flush()
    return {"visible_to_roles": list(node.visible_to_roles)}
