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
from src.prompts import AnalystSpec, VolumeTarget, list_analysts, load_component

# 개인 에이전트의 목표 분량 3단 — 폼에서 고르는 값이 여기서 실제 목표가 된다.
# 시스템 카탈로그 실측 범위(정책분석 15000~22500 등)에 맞춰 잡았다.
VOLUME_PRESETS: dict[str, VolumeTarget] = {
    "short": VolumeTarget(min_chars=4000, max_chars=7000, pages="2~4p"),
    "normal": VolumeTarget(min_chars=8000, max_chars=12000, pages="5~8p"),
    "long": VolumeTarget(min_chars=15000, max_chars=22500, pages="10~15p"),
}

# 작성 규칙 슬롯 — 시스템 조각 3종의 고정 순서. 개인 규칙은 base_ref로 이 중
# 하나를 교체하거나(슬롯 교체), base_ref 없이 뒤에 덧붙는다(추가 규칙).
RULE_SLOTS: tuple[str, ...] = ("agent_source_rules", "agent_visual_rules", "agent_writing_style")


def volume_from_spec(spec: dict | None) -> VolumeTarget | None:
    """spec.volume(short|normal|long) → 목표 분량. 값이 없거나 모르면 None."""
    if not isinstance(spec, dict):
        return None
    return VOLUME_PRESETS.get(str(spec.get("volume") or ""))


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
    spec: dict | None = None,
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
        spec=spec or {},
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
    spec: dict | None = None,
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
    if spec is not None:
        row.spec = spec
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
                        # 분량은 고쳐 적었으면 그 값, 아니면 원본 승계.
                        "volume_target": volume_from_spec(ov.spec) or spec.volume_target,
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
                    queries=[q for q in (p.spec or {}).get("queries", []) if isinstance(q, str)],
                    prompt=p.content,
                    # 목표 분량이 없으면 절 분량 목표가 통째로 사라져(=짧은 절) 개인
                    # 에이전트를 배정할수록 손해였다. 지정 없으면 '보통'을 기본으로 준다.
                    volume_target=volume_from_spec(p.spec) or VOLUME_PRESETS["normal"],
                )
            )
    return merged


async def resolve_rules(
    session: AsyncSession, owner_id: UUID, selected_ids: list[UUID] | None = None
) -> list[str]:
    """작성 규칙 텍스트 목록 — 시스템 3종 슬롯에 선택된 개인 규칙을 얹어 돌려준다.

    selected_ids가 None이거나 비면 회사 표준 3종 그대로다(기존 동작). 선택된 개인
    규칙 중 base_ref가 슬롯 이름이면 그 자리를 교체하고, base_ref가 없으면 맨 뒤에
    추가 규칙으로 붙인다. 규칙은 보고서 단위 계약이라 프로젝트에서 한 번 고른다.
    """
    if not selected_ids:
        return [load_component(name) for name in RULE_SLOTS]
    rows = (
        (
            await session.execute(
                select(UserPrompt).where(
                    UserPrompt.owner_id == owner_id,
                    UserPrompt.kind == "rule",
                    UserPrompt.id.in_(list(selected_ids)),
                )
            )
        )
        .scalars()
        .all()
    )
    overrides = {r.base_ref: r for r in rows if r.base_ref in RULE_SLOTS}
    out = [
        overrides[name].content if name in overrides else load_component(name)
        for name in RULE_SLOTS
    ]
    out.extend(r.content for r in rows if r.base_ref not in RULE_SLOTS)
    return out
