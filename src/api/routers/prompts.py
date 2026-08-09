"""프롬프트 API — 개인 프롬프트 CRUD + 시스템 카탈로그 읽기전용.

라이브러리 '프롬프트' 폴더(개인 편집)와 회사 공유 '시스템 프롬프트'(읽기전용)의 백엔드.
해석은 개인 → 시스템 폴백(src/services/prompts). kind='agent'/'rule'.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_active_user
from src.api.dependencies.db import get_async_session
from src.api.schemas.prompt import (
    PersonalPromptCreate,
    PersonalPromptRead,
    PersonalPromptUpdate,
    SystemPromptRead,
)
from src.core.exceptions import NotFoundError, ValidationError
from src.db.models.user import User
from src.prompts import list_analysts, list_components, load_analyst, load_component
from src.services.prompts import (
    create_personal,
    delete_personal,
    get_personal,
    list_personal,
    update_personal,
)

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _validate_base_ref(kind: str, base_ref: str | None) -> None:
    """base_ref가 실제 시스템 항목을 가리키는지 확인(댕글링 오버라이드 방지)."""
    if base_ref is None:
        return
    if kind == "agent":
        try:
            load_analyst(base_ref)
        except KeyError:
            raise ValidationError(
                message=f"알 수 없는 시스템 에이전트: {base_ref}", code="UNKNOWN_BASE_REF"
            ) from None
    else:  # rule
        # 카탈로그 이름 집합과 대조한다. load_component(base_ref)로 검증하면 사용자 입력이
        # 파일 경로로 조립돼(../ 순회) *.md 존재 여부 오라클이 되므로, 멤버십으로만 확인한다.
        if base_ref not in list_components():
            raise ValidationError(
                message=f"알 수 없는 시스템 작성 규칙: {base_ref}", code="UNKNOWN_BASE_REF"
            )


# ─── 개인 프롬프트 CRUD (소유자 스코프) ──────────────────────────────────────


@router.get("/personal", response_model=list[PersonalPromptRead])
async def list_my_prompts(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
    kind: str | None = None,
) -> list[PersonalPromptRead]:
    rows = await list_personal(session, current_user.id, kind)
    return [PersonalPromptRead.model_validate(r) for r in rows]


@router.post("/personal", response_model=PersonalPromptRead, status_code=status.HTTP_201_CREATED)
async def create_my_prompt(
    data: PersonalPromptCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PersonalPromptRead:
    _validate_base_ref(data.kind, data.base_ref)
    row = await create_personal(
        session,
        current_user.id,
        kind=data.kind,
        name=data.name,
        content=data.content,
        base_ref=data.base_ref,
        cat=data.cat,
        description=data.description,
        spec=data.spec.model_dump(exclude_none=True),
    )
    return PersonalPromptRead.model_validate(row)


@router.get("/personal/{prompt_id}", response_model=PersonalPromptRead)
async def get_my_prompt(
    prompt_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PersonalPromptRead:
    row = await get_personal(session, current_user.id, prompt_id)
    return PersonalPromptRead.model_validate(row)


@router.patch("/personal/{prompt_id}", response_model=PersonalPromptRead)
async def update_my_prompt(
    prompt_id: UUID,
    data: PersonalPromptUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> PersonalPromptRead:
    row = await update_personal(
        session,
        current_user.id,
        prompt_id,
        name=data.name,
        content=data.content,
        cat=data.cat,
        description=data.description,
        spec=data.spec.model_dump(exclude_none=True) if data.spec is not None else None,
    )
    return PersonalPromptRead.model_validate(row)


@router.delete("/personal/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_prompt(
    prompt_id: UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    await delete_personal(session, current_user.id, prompt_id)


# ─── 시스템 카탈로그 (읽기전용) ──────────────────────────────────────────────


@router.get("/system", response_model=list[SystemPromptRead])
async def list_system_prompts(
    _: Annotated[User, Depends(get_current_active_user)],
    kind: str | None = None,
) -> list[SystemPromptRead]:
    """시스템 프롬프트 카탈로그(내용 포함). kind=agent/rule 필터."""
    items: list[SystemPromptRead] = []
    if kind in (None, "agent"):
        items.extend(
            SystemPromptRead(
                ref=a.id,
                kind="agent",
                name=a.name,
                content=a.prompt,
                cat=a.cat,
                description=a.desc,
            )
            for a in list_analysts()
        )
    if kind in (None, "rule"):
        items.extend(
            SystemPromptRead(ref=name, kind="rule", name=name, content=load_component(name))
            for name in list_components()
        )
    return items


@router.get("/system/{kind}/{ref}", response_model=SystemPromptRead)
async def get_system_prompt(
    kind: str,
    ref: str,
    _: Annotated[User, Depends(get_current_active_user)],
) -> SystemPromptRead:
    if kind == "agent":
        try:
            a = load_analyst(ref)
        except KeyError:
            raise NotFoundError(
                message="에이전트를 찾을 수 없습니다", code="PROMPT_NOT_FOUND"
            ) from None
        return SystemPromptRead(
            ref=a.id, kind="agent", name=a.name, content=a.prompt, cat=a.cat, description=a.desc
        )
    if kind == "rule":
        try:
            content = load_component(ref)
        except KeyError:
            raise NotFoundError(
                message="작성 규칙을 찾을 수 없습니다", code="PROMPT_NOT_FOUND"
            ) from None
        return SystemPromptRead(ref=ref, kind="rule", name=ref, content=content)
    raise ValidationError(message=f"알 수 없는 종류: {kind}", code="INVALID_PROMPT_KIND")
