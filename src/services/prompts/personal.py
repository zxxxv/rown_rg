"""개인 프롬프트 레이어 — 저장(CRUD) + 층화 해석(개인 → 시스템 폴백).

시스템 카탈로그(src/prompts 파일)가 단일 진실이고, user_prompts는 그 위 오버레이다.
- kind='agent': 분석 에이전트. base_ref가 시스템 에이전트(id/name)를 가리키면 그 프롬프트를
  덮어쓰고, 없으면 새 개인 에이전트로 추가된다.
- kind='rule' : 작성 규칙(components/*.md 대응). 트리에서 개인/시스템을 나란히 노출한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.db.models.user_prompt import UserPrompt
from src.prompts import AnalystSpec, list_analysts

VALID_KINDS = ("agent", "rule")


def _check_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise ValidationError(
            message=f"알 수 없는 프롬프트 종류: {kind} (가능: {', '.join(VALID_KINDS)})",
            code="INVALID_PROMPT_KIND",
        )


async def list_personal(
    session: AsyncSession, owner_id: UUID, kind: str | None = None
) -> list[UserPrompt]:
    """내 개인 프롬프트 목록(최신순). kind로 agent/rule 필터."""
    stmt = select(UserPrompt).where(UserPrompt.owner_id == owner_id)
    if kind is not None:
        _check_kind(kind)
        stmt = stmt.where(UserPrompt.kind == kind)
    stmt = stmt.order_by(UserPrompt.updated_at.desc())
    return list((await session.execute(stmt)).scalars())


async def get_personal(session: AsyncSession, owner_id: UUID, prompt_id: UUID) -> UserPrompt:
    """개인 프롬프트 1건 로드 + 소유자 확인."""
    row = await session.get(UserPrompt, prompt_id)
    if row is None or row.owner_id != owner_id:
        raise NotFoundError(message="프롬프트를 찾을 수 없습니다", code="PROMPT_NOT_FOUND")
    return row


async def create_personal(
    session: AsyncSession,
    owner_id: UUID,
    *,
    kind: str,
    name: str,
    content: str,
    base_ref: str | None = None,
    cat: str | None = None,
    description: str | None = None,
) -> UserPrompt:
    _check_kind(kind)
    row = UserPrompt(
        owner_id=owner_id,
        kind=kind,
        name=name,
        content=content,
        base_ref=base_ref,
        cat=cat,
        description=description,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_personal(
    session: AsyncSession,
    owner_id: UUID,
    prompt_id: UUID,
    *,
    name: str | None = None,
    content: str | None = None,
    cat: str | None = None,
    description: str | None = None,
) -> UserPrompt:
    """개인 프롬프트 수정 — kind·base_ref는 불변(오버라이드 대상은 생성 시 확정)."""
    row = await get_personal(session, owner_id, prompt_id)
    if name is not None:
        row.name = name
    if content is not None:
        row.content = content
    if cat is not None:
        row.cat = cat
    if description is not None:
        row.description = description
    await session.flush()
    await session.refresh(row)
    return row


async def delete_personal(session: AsyncSession, owner_id: UUID, prompt_id: UUID) -> None:
    row = await get_personal(session, owner_id, prompt_id)
    await session.delete(row)


async def resolve_analysts(session: AsyncSession, owner_id: UUID) -> list[AnalystSpec]:
    """개인 → 시스템 폴백으로 병합한 분석 에이전트 목록.

    base_ref가 시스템 에이전트(id/name)를 가리키는 개인 에이전트는 그 프롬프트를 덮어쓰고,
    base_ref 없는 개인 에이전트는 뒤에 새로 붙는다(id=`u-<uuid>`).
    """
    system = list_analysts()
    personals = (
        (
            await session.execute(
                select(UserPrompt).where(
                    UserPrompt.owner_id == owner_id, UserPrompt.kind == "agent"
                )
            )
        )
        .scalars()
        .all()
    )
    overrides = {p.base_ref: p for p in personals if p.base_ref}

    merged: list[AnalystSpec] = []
    for spec in system:
        ov = overrides.get(spec.id) or overrides.get(spec.name)
        if ov is not None:
            merged.append(
                spec.model_copy(
                    update={
                        "prompt": ov.content,
                        "desc": ov.description or spec.desc,
                        "cat": ov.cat or spec.cat,
                    }
                )
            )
        else:
            merged.append(spec)

    for p in personals:
        if not p.base_ref:
            merged.append(
                AnalystSpec(
                    id=f"u-{p.id}",
                    name=p.name,
                    cat=p.cat or "개인",
                    desc=p.description or "",
                    queries=[],
                    prompt=p.content,
                    volume_target=None,
                )
            )
    return merged
