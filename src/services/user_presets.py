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
from src.db.models.user import User
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


async def list_public_presets(
    session: AsyncSession, viewer_id: UUID | None = None
) -> list[tuple[UserPreset, str]]:
    """공개된 남의 목차 프리셋 + 소유자 이름. viewer_id를 주면 그 사람 것은 뺀다.

    자기 것은 개인 층에서 이미 나오므로 공개 층에서 또 넣으면 목록에 두 벌 뜬다
    (에이전트 공유와 같은 규약 — services/prompts/personal.list_public_agents).
    """
    stmt = (
        select(UserPreset, User.name)
        .join(User, User.id == UserPreset.owner_id)
        .where(UserPreset.is_public.is_(True))
    )
    if viewer_id is not None:
        stmt = stmt.where(UserPreset.owner_id != viewer_id)
    stmt = stmt.order_by(UserPreset.name)
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def get_readable_preset(
    session: AsyncSession, viewer_id: UUID, preset_id: UUID, *, is_admin: bool = False
) -> UserPreset:
    """볼 수 있는 프리셋 1건 — 내 것이거나 공개된 것, 관리자는 전부(감사·지원 미러).

    골격 조회·프로젝트 생성 검증이 이걸 쓴다. 소유자 전용(get_user_preset)은 수정·삭제
    경로가 계속 쓴다 — 남의 공개 프리셋을 고칠 수 있으면 공유가 아니라 공용 편집이 된다.
    is_admin은 라이브러리 '사용자별 자료'에서 열람만 뚫는다 — 트리가 노드를 보여주면서
    상세가 404를 내면 화면에선 "안 보인다"가 된다(2026-08-20 운영 실측).
    """
    row = await session.get(UserPreset, preset_id)
    if row is None or (row.owner_id != viewer_id and not row.is_public and not is_admin):
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
    is_public: bool = False,
) -> UserPreset:
    await _check_name_free(session, owner_id, name)
    row = UserPreset(
        owner_id=owner_id,
        name=name,
        description=description,
        outline=outline,
        is_public=is_public,
    )
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
    is_public: bool | None = None,
) -> UserPreset:
    row = await get_user_preset(session, owner_id, preset_id)
    await _check_name_free(session, owner_id, name, exclude_id=preset_id)
    row.name = name
    row.description = description
    row.outline = outline
    if is_public is not None:
        row.is_public = is_public
    await session.flush()
    await session.refresh(row)
    return row


async def import_public_preset(
    session: AsyncSession, owner_id: UUID, source_id: UUID
) -> UserPreset:
    """공개된 남의 목차 프리셋을 내 것으로 복제한다(에이전트 가져오기와 같은 규약).

    공개 여부는 승계하지 않는다 — 가져왔다고 내 이름으로 자동 재공개되면 곤란하다.
    이름이 겹치면 "(사본)"을 붙인다(owner_id+name 유니크).
    """
    src = await session.get(UserPreset, source_id)
    if src is None or not (src.is_public or src.owner_id == owner_id):
        raise NotFoundError(message="가져올 프리셋을 찾을 수 없습니다", code="PRESET_NOT_FOUND")
    taken = set(
        (await session.execute(select(UserPreset.name).where(UserPreset.owner_id == owner_id)))
        .scalars()
        .all()
    )
    name = src.name
    if name in taken:
        name = f"{src.name} (사본)"
        n = 2
        while name in taken:
            name = f"{src.name} (사본 {n})"
            n += 1
    row = UserPreset(
        owner_id=owner_id,
        name=name,
        description=src.description,
        outline=dict(src.outline or {}),
        is_public=False,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def delete_user_preset(session: AsyncSession, owner_id: UUID, preset_id: UUID) -> None:
    row = await get_user_preset(session, owner_id, preset_id)
    await session.delete(row)
    await session.flush()
