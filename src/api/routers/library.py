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

import json
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
from src.api.dependencies.permissions import ADMINS, require_role, require_writer
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
from src.api.uploads import read_validated_upload
from src.core.config import settings
from src.core.exceptions import AuthorizationError, NotFoundError, ValidationError
from src.core.types import Role
from src.db.models.chunk import Chunk
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from src.prompts import catalog_file_stat, list_analysts, list_components, list_presets
from src.services.export.report import export_filename
from src.services.prompts import list_personal, list_public_agents
from src.services.user_presets import list_public_presets, list_user_presets

router = APIRouter(prefix="/library", tags=["library"])

ALL_ROLES: list[str] = [r.value for r in Role]
VALID_SOURCE_KINDS = {"gov", "academic", "media", "library", "upload", "web_search"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _is_admin(user: User) -> bool:
    return user.role in (Role.SUPER_ADMIN.value, Role.ADMIN.value)


def _can_view(node: LibraryNode, user: User) -> bool:
    # 개인 노드는 소유자 본인 + 관리자(감사·지원용). 관리자는 '사용자별 자료'로 열람한다.
    if node.is_personal:
        return node.created_by == user.id or _is_admin(user)
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


def _source_size(src: ProjectSource, content_md: str, node_sizes: dict[UUID, int]) -> int:
    """자료 1건의 표시 크기.

    ① 색인 때 기록한 값 ② 업로드 파일이면 디스크에서 즉시 측정 ③ 라이브러리 참조면
    원본 노드의 크기 ④ 웹 수집이면 본문 바이트 길이. ②③은 옛 자료(기록 없음)도
    재색인 없이 보이게 하려는 폴백이다.
    """
    recorded = int((src.metadata_ or {}).get("size_bytes") or 0)
    if recorded:
        return recorded
    if src.upload_path:
        try:
            return Path(src.upload_path).stat().st_size
        except OSError:
            return 0
    if src.library_node_id is not None:
        return node_sizes.get(src.library_node_id, 0)
    return len(content_md.encode("utf-8"))


def _source_file(
    src: ProjectSource, pid: UUID, owner_name: str, node_sizes: dict[UUID, int] | None = None
) -> LibraryTreeFile:
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
            # 웹 수집은 물리 파일이 없어 본문 바이트 길이를, 업로드·라이브러리 참조는
            # 색인 때 기록한 실제 파일 크기를 쓴다. 후자를 안 쓰면 목록이 0 B로 뜨고
            # 상위 폴더 합계까지 0이 된다(2026-08-10 지적).
            size_bytes=_source_size(src, content_md, node_sizes or {}),
            page_count=(src.metadata_ or {}).get("page_count"),
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
    node_sizes: dict[UUID, int] | None = None,
) -> LibraryTreeFolder:
    """프로젝트 1건 → 가상 폴더(완성본 + AI수집/업로드/참조). project_sources·export 합성."""
    base = f"proj-{pid}"
    children: list[LibraryTreeFolder | LibraryTreeFile] = []

    # 완성본 — export가 렌더된 프로젝트만(결정적 경로 <export_dir>/<id>.r<버전>.hwpx).
    export_path = Path(settings.export_dir) / export_filename(pid)
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
            _source_file(s, pid, owner_name, node_sizes)
            for s in sources
            if s.source_type == source_type
        ]
        children.append(
            LibraryTreeFolder(
                id=f"{base}-{suffix}",
                name=label,
                virtual=True,
                owner_name=owner_name,
                children=files,
            )
        )

    return LibraryTreeFolder(
        id=base, name=title, virtual=True, owner_name=owner_name, children=children
    )


async def _build_projects_folder(
    session: AsyncSession, user_id: UUID, user_name: str, *, prefix: str = "me"
) -> LibraryTreeFolder:
    """개인 루트의 '프로젝트' 가상 폴더 — 해당 사용자 소유 프로젝트만(소유자 스코프).

    관리자도 라이브러리에선 자기 프로젝트만 본다(전체 조회는 admin 대시보드/scope=all).
    이로써 라이브러리와 프로젝트 목록(기본 scope=mine)의 가시성이 일치한다. prefix로 폴더
    id를 네임스페이스한다(관리자 '사용자별 자료' 뷰에서 사용자 간 id 중복 방지).
    """
    projs = (
        await session.execute(
            select(Project.id, Project.title, Project.updated_at)
            .where(Project.owner_id == user_id)
            .order_by(Project.created_at.desc())
        )
    ).all()
    empty = LibraryTreeFolder(
        id=f"{prefix}-projects",
        name="프로젝트",
        virtual=True,
        owner_name=user_name,
        children=[],
    )
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

    # 라이브러리 참조 자료는 원본 노드가 크기를 안다 — 한 번에 모아 온다(N+1 방지).
    ref_ids = {s.library_node_id for s in srcs if s.library_node_id is not None}
    node_sizes: dict[UUID, int] = {}
    if ref_ids:
        rows = (
            await session.execute(
                select(LibraryNode.id, LibraryNode.file_size).where(LibraryNode.id.in_(ref_ids))
            )
        ).all()
        node_sizes = {nid: int(size or 0) for nid, size in rows}

    empty.children = [
        _project_folder(pid, title, updated_at, by_proj[pid], user_name, node_sizes)
        for pid, title, updated_at in projs
    ]
    return empty


# 시스템 프롬프트 노드 표시용 고정 타임스탬프(파일 카탈로그라 등록 시각이 없다).
_PROMPT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _prompt_file(
    node_id: str,
    name: str,
    ref: PromptRef,
    registered_by: str,
    registered_at: datetime,
    *,
    size_bytes: int = 0,
) -> LibraryTreeFile:
    """프롬프트 파일 노드(가상). 프론트는 prompt 마커로 에디터를 연다."""
    return LibraryTreeFile(
        id=node_id,
        name=name,
        virtual=True,
        prompt=ref,
        file_meta=LibraryFileMeta(
            size_bytes=size_bytes,
            registered_at=registered_at,
            registered_by=registered_by,
            source_kind="library",
            visible_to_roles=ALL_ROLES,
        ),
    )


async def _build_prompts_folder(
    session: AsyncSession,
    user_id: UUID,
    user_name: str,
    *,
    prefix: str = "me",
    editable: bool = True,
) -> LibraryTreeFolder:
    """개인 루트의 '프롬프트' 폴더 — 해당 사용자의 에이전트/작성 규칙(개인 DB).

    prefix로 폴더 id를 네임스페이스한다(사용자별 자료 뷰 중복 방지). 프롬프트 파일 id는
    개인 프롬프트 UUID라 사용자 간 충돌하지 않는다. editable=False는 관리자 미러 —
    열람만 되고 수정 경로는 소유자 전용이라, 편집 가능으로 내리면 저장에서 404가 난다.
    크기는 본문 바이트 수 — 0 B 고정으로 보이던 문제(2026-08-20 운영 지적).
    """
    personals = await list_personal(session, user_id)

    def node(p: object) -> LibraryTreeFile:
        return _prompt_file(
            f"uprompt-{p.id}",  # type: ignore[attr-defined]
            p.name,  # type: ignore[attr-defined]
            PromptRef(scope="personal", kind=p.kind, ref=str(p.id), editable=editable),  # type: ignore[attr-defined]
            user_name,
            p.updated_at,  # type: ignore[attr-defined]
            size_bytes=len((p.content or "").encode("utf-8")),  # type: ignore[attr-defined]
        )

    agents: list[LibraryTreeFolder | LibraryTreeFile] = [
        node(p) for p in personals if p.kind == "agent"
    ]
    rules: list[LibraryTreeFolder | LibraryTreeFile] = [
        node(p) for p in personals if p.kind == "rule"
    ]
    presets: list[LibraryTreeFolder | LibraryTreeFile] = [
        _prompt_file(
            f"upreset-{row.id}",
            row.name,
            PromptRef(scope="personal", kind="preset", ref=str(row.id), editable=editable),
            user_name,
            row.updated_at,
            size_bytes=len(json.dumps(row.outline or {}, ensure_ascii=False).encode("utf-8")),
        )
        for row in await list_user_presets(session, user_id)
    ]
    return LibraryTreeFolder(
        id=f"{prefix}-prompts",
        name="프롬프트",
        virtual=True,
        children=[
            LibraryTreeFolder(
                id=f"{prefix}-agents", name="내 에이전트", virtual=True, children=agents
            ),
            LibraryTreeFolder(
                id=f"{prefix}-rules", name="내 작성 규칙", virtual=True, children=rules
            ),
            # 목차 프리셋도 '내가 만들어 재사용하는 자산'이라 같은 자리에 둔다 —
            # 프롬프트 화면에서만 보이면 라이브러리가 개인 자산의 전체 그림이 못 된다.
            LibraryTreeFolder(
                id=f"{prefix}-presets", name="내 목차 프리셋", virtual=True, children=presets
            ),
        ],
    )


def _system_prompts_folder() -> LibraryTreeFolder:
    """회사 공유의 '시스템 프롬프트' 폴더 — 에이전트/작성 규칙(파일 카탈로그, 읽기전용)."""

    def _sys(node_id: str, name: str, kind: str, ref: str) -> LibraryTreeFile:
        # 카탈로그 원본 파일의 크기·수정시각을 그대로 노출 — 0 B·고정 날짜로 보이던
        # 문제(2026-08-09 지적)를 없앤다. 파일이 없으면 종전 표시로 폴백.
        stat = catalog_file_stat(kind, ref)
        size = stat[0] if stat else 0
        at = datetime.fromtimestamp(stat[1], tz=UTC) if stat else _PROMPT_EPOCH
        return _prompt_file(
            node_id,
            name,
            PromptRef(scope="system", kind=kind, ref=ref, editable=False),
            "시스템",
            at,
            size_bytes=size,
        )

    agents: list[LibraryTreeFolder | LibraryTreeFile] = [
        _sys(f"sysagent-{a.id}", a.name, "agent", a.id) for a in list_analysts()
    ]
    rules: list[LibraryTreeFolder | LibraryTreeFile] = [
        _sys(f"syscomp-{name}", name, "rule", name) for name in list_components()
    ]
    presets: list[LibraryTreeFolder | LibraryTreeFile] = []
    for p in list_presets():
        # 파일명은 name과 같다 — id로 stat이 안 잡히면 name으로 한 번 더.
        stat = catalog_file_stat("preset", p.id) or catalog_file_stat("preset", p.name)
        presets.append(
            _prompt_file(
                f"syspreset-{p.id}",
                p.name,
                PromptRef(scope="system", kind="preset", ref=p.id, editable=False),
                "시스템",
                datetime.fromtimestamp(stat[1], tz=UTC) if stat else _PROMPT_EPOCH,
                size_bytes=stat[0] if stat else 0,
            )
        )
    return LibraryTreeFolder(
        id="sys-prompts",
        name="시스템 프롬프트",
        virtual=True,
        children=[
            LibraryTreeFolder(
                id="sys-agents", name="에이전트", virtual=True, owner_name="시스템", children=agents
            ),
            LibraryTreeFolder(
                id="sys-rules", name="작성 규칙", virtual=True, owner_name="시스템", children=rules
            ),
            LibraryTreeFolder(
                id="sys-presets",
                name="목차 프리셋",
                virtual=True,
                owner_name="시스템",
                children=presets,
            ),
        ],
    )


async def _shared_folder(session: AsyncSession, viewer_id: UUID) -> LibraryTreeFolder:
    """회사 공유의 '동료 공개' 폴더 — 남이 공개 토글을 켠 개인 자산(읽기 전용).

    종류별로 묶는다(2026-08-19 사용자 결정) — 시스템 프롬프트 폴더와 같은 모양이라
    "무엇을 찾을 때 어디를 여는가"가 하나로 통일된다. 사람별로 묶으면 사람이 늘수록
    폴더가 길어진다.

    여기서는 고칠 수 없다(editable=False). 고치려면 '내 것으로 가져오기'로 복제한다 —
    남의 자산을 라이브러리에서 직접 고칠 수 있으면 공유가 아니라 공용 편집이 된다.
    """
    agents: list[LibraryTreeFolder | LibraryTreeFile] = [
        _prompt_file(
            f"shared-agent-{row.id}",
            row.name,
            PromptRef(
                scope="shared",
                kind="agent",
                ref=str(row.id),
                editable=False,
                owner_name=owner_name,
                importable=True,
            ),
            owner_name,
            row.updated_at,
            size_bytes=len((row.content or "").encode("utf-8")),
        )
        for row, owner_name in await list_public_agents(session, viewer_id)
    ]
    presets: list[LibraryTreeFolder | LibraryTreeFile] = [
        _prompt_file(
            f"shared-preset-{row.id}",
            row.name,
            PromptRef(
                scope="shared",
                kind="preset",
                ref=str(row.id),
                editable=False,
                owner_name=owner_name,
                importable=True,
            ),
            owner_name,
            row.updated_at,
            size_bytes=len(json.dumps(row.outline or {}, ensure_ascii=False).encode("utf-8")),
        )
        for row, owner_name in await list_public_presets(session, viewer_id)
    ]
    return LibraryTreeFolder(
        id="shared-prompts",
        name="동료 공개",
        virtual=True,
        children=[
            LibraryTreeFolder(id="shared-agents", name="에이전트", virtual=True, children=agents),
            LibraryTreeFolder(
                id="shared-presets", name="목차 프리셋", virtual=True, children=presets
            ),
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
        creator = n.__dict__.get("creator")
        return LibraryTreeFolder(
            id=str(n.id),
            name=n.name,
            owner_name=creator.name if creator is not None else None,
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

    # 관리자 전용 — 본인 제외 모든 가입 사용자를 폴더로 나열한다. 각 사용자 폴더는 개인 루트와
    # 같은 구조(프로젝트·프롬프트·내 자료)를 그대로 미러링해 감사·지원 시 한 곳에서 열람한다.
    # 자료가 없어도 폴더는 항상 나타난다(사용자별 진입점). prefix로 사용자 간 id 중복을 막는다.
    admin_users_group: LibraryTreeFolder | None = None
    if _is_admin(current_user):
        nodes_by_user: dict[UUID, list[LibraryNode]] = defaultdict(list)
        for n in children_map[None]:
            if n.is_personal and n.created_by is not None and n.created_by != current_user.id:
                nodes_by_user[n.created_by].append(n)
        user_rows = (
            await session.execute(
                select(User.id, User.name, User.is_active)
                .where(User.id != current_user.id)
                .order_by(User.name)
            )
        ).all()
        if user_rows:
            user_folders: list[LibraryTreeFolder | LibraryTreeFile] = []
            for uid, name, is_active in user_rows:
                prefix = f"u{uid}"
                projects_f = await _build_projects_folder(session, uid, name, prefix=prefix)
                # 미러는 열람 전용 — editable로 내리면 편집기가 열리고 저장에서 404가 난다
                # (수정 경로는 소유자 전용 유지).
                prompts_f = await _build_prompts_folder(
                    session, uid, name, prefix=prefix, editable=False
                )
                files_f = LibraryTreeFolder(
                    id=f"{prefix}-files",
                    name="내 자료",
                    virtual=True,
                    owner_name=name,
                    children=[b for n in nodes_by_user.get(uid, []) if (b := build(n)) is not None],
                )
                user_folders.append(
                    LibraryTreeFolder(
                        id=f"admin-user-{uid}",
                        name=f"{name}{'' if is_active else ' (비활성)'}",
                        virtual=True,
                        owner_name=name,
                        children=[projects_f, prompts_f, files_f],
                    )
                )
            admin_users_group = LibraryTreeFolder(
                id="admin-users",
                name="사용자별 자료 (관리자)",
                virtual=True,
                children=user_folders,
            )

    projects_folder = await _build_projects_folder(session, current_user.id, current_user.name)
    prompts_folder = await _build_prompts_folder(session, current_user.id, current_user.name)

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
        children=[
            *company_roots,
            _system_prompts_folder(),
            await _shared_folder(session, current_user.id),
        ],
    )

    tree: list[LibraryTreeFolder | LibraryTreeFile] = [me_root, company_root]
    if admin_users_group is not None:
        tree.append(admin_users_group)
    return LibraryTreeResponse(tree=tree)


@router.post(
    "/folders",
    response_model=LibraryTreeFolder,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    data: FolderCreateRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
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
    current_user: Annotated[User, Depends(require_writer)],
    parent_id: Annotated[UUID | None, Form()] = None,
    is_personal: Annotated[bool, Form()] = False,
) -> LibraryTreeFile:
    parent = await _get_parent_folder(session, parent_id)
    # 상위 폴더가 있으면 개인/공유 성격 상속, 최상위면 요청값(내 자료=True/회사 공유=False).
    personal = parent.is_personal if parent is not None else is_personal
    safe_name, content = await read_validated_upload(file, max_bytes=MAX_UPLOAD_BYTES)

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
    가시성: 소속 프로젝트 소유자 + 관리자. '사용자별 자료' 미러가 타인 프로젝트 트리를
    관리자에게 보여주므로, 여기만 소유자 전용이면 트리엔 보이는데 원문만 404가 나
    화면에선 "안 보인다"가 된다(2026-08-20 사용자 결정: 관리자는 전부 열람).
    그 외 타인 자료·미존재는 존재 은닉을 위해 모두 404로 통일한다.
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
    if owner_id != current_user.id and not _is_admin(current_user):
        raise NotFoundError(message="자료를 찾을 수 없습니다", code="SOURCE_NOT_FOUND")
    content_md = (src.metadata_ or {}).get("content_md") or ""
    if not content_md.strip():
        raise NotFoundError(
            message="수집된 본문이 없습니다(메타데이터만 있는 자료)", code="SOURCE_BODY_MISSING"
        )
    # 화면은 **색인된 것과 같은 것**을 보여준다. 종전엔 수집 원문을 날것으로 돌려줘
    # 댓글 폼·푸터·저작권이 그대로 보였다 - 실제로는 색인 때 배제된 대목인데 화면만
    # 보고 "이런 게 다 들어가는구나"로 읽힌다(2026-08-27 사용자 지적).
    # 청크가 있으면 배제되지 않은 것만 이어 붙이고, 아직 색인 전이면 줄 단위 청소만 한다.
    from src.workflows.stages import clean_web_markdown

    chunk_rows = (
        await session.execute(
            select(Chunk.content, Chunk.metadata_)
            .where(Chunk.source_id == source_id)
            .order_by(Chunk.chunk_index)
        )
    ).all()
    excluded_kinds: list[str] = []
    excluded_chars = 0
    if chunk_rows:
        kept: list[str] = []
        for content, meta in chunk_rows:
            reason = (meta or {}).get("excluded")
            if reason:
                excluded_chars += len(content or "")
                if reason not in excluded_kinds:
                    excluded_kinds.append(str(reason))
                continue
            kept.append(content or "")
        body = (chr(10) * 2).join(x for x in kept if x.strip())
        # 전부 배제된 자료는 원문을 보여준다 - 빈 화면보다 "왜 다 빠졌는지"를 보고
        # 판단할 수 있어야 한다(필터 오판을 사람이 잡는 유일한 자리다).
        content_md = body or clean_web_markdown(content_md)
    else:
        content_md = clean_web_markdown(content_md) or content_md

    return SourceContentResponse(
        title=src.title,
        url=src.url,
        reliability=src.reliability,
        content_md=content_md,
        char_count=len(content_md),
        byte_count=len(content_md.encode("utf-8")),
        excluded_kinds=excluded_kinds,
        excluded_chars=excluded_chars,
    )


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(require_writer)],
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
