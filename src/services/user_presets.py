"""개인 목차 프리셋 — 소유자 스코프 CRUD와 "u:<uuid>" 키 해석.

시스템 프리셋(src/prompts 파일)이 단일 진실인 카탈로그 위의 개인 오버레이.
생성 폼의 preset 값과 GET /presets/{key}가 같은 키("u:<uuid>")를 쓰므로,
프론트는 시스템 프리셋과 동일한 코드 경로로 골격을 로드한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.db.models.user_preset import UserPreset

PERSONAL_PRESET_PREFIX = "u:"


def personal_preset_key(preset_id: UUID) -> str:
    return f"{PERSONAL_PRESET_PREFIX}{preset_id}"


def parse_personal_key(preset_key: str) -> UUID | None:
    """ "u:<uuid>"면 UUID를, 아니면(시스템 키) None을 돌려준다."""
    if not preset_key.startswith(PERSONAL_PRESET_PREFIX):
        return None
    try:
        return UUID(preset_key[len(PERSONAL_PRESET_PREFIX) :])
    except ValueError:
        return None


async def list_user_presets(session: AsyncSession, owner_id: UUID) -> list[UserPreset]:
    rows = await session.execute(
        select(UserPreset).where(UserPreset.owner_id == owner_id).order_by(UserPreset.name)
    )
    return list(rows.scalars())


async def get_user_preset(session: AsyncSession, owner_id: UUID, preset_id: UUID) -> UserPreset:
    row = await session.get(UserPreset, preset_id)
    if row is None or row.owner_id != owner_id:
        raise NotFoundError(message="프리셋을 찾을 수 없습니다", code="PRESET_NOT_FOUND")
    return row


async def _check_name_free(
    session: AsyncSession, owner_id: UUID, name: str, exclude_id: UUID | None = None
) -> None:
    q = select(UserPreset.id).where(UserPreset.owner_id == owner_id, UserPreset.name == name)
    if exclude_id is not None:
        q = q.where(UserPreset.id != exclude_id)
    if (await session.execute(q)).first() is not None:
        raise ValidationError(
            message=f"같은 이름의 프리셋이 이미 있습니다: {name}", code="DUPLICATE_PRESET_NAME"
        )


async def create_user_preset(
    session: AsyncSession,
    owner_id: UUID,
    *,
    name: str,
    description: str | None,
    outline: dict,
) -> UserPreset:
    await _check_name_free(session, owner_id, name)
    row = UserPreset(owner_id=owner_id, name=name, description=description, outline=outline)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_user_preset(
    session: AsyncSession,
    owner_id: UUID,
    preset_id: UUID,
    *,
    name: str,
    description: str | None,
    outline: dict,
) -> UserPreset:
    row = await get_user_preset(session, owner_id, preset_id)
    await _check_name_free(session, owner_id, name, exclude_id=preset_id)
    row.name = name
    row.description = description
    row.outline = outline
    await session.flush()
    await session.refresh(row)
    return row


async def delete_user_preset(session: AsyncSession, owner_id: UUID, preset_id: UUID) -> None:
    row = await get_user_preset(session, owner_id, preset_id)
    await session.delete(row)
    await session.flush()
