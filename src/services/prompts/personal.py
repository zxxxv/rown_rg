"""개인 프롬프트 레이어 — 저장(CRUD) + 층화 해석(개인 → 시스템 폴백).

시스템 카탈로그(src/prompts 파일)가 단일 진실이고, user_prompts는 그 위 오버레이다.
- kind='agent': 분석 에이전트. base_ref가 시스템 에이전트(id/name)를 가리키면 그 프롬프트를
  덮어쓰고, 없으면 새 개인 에이전트로 추가된다.
- kind='rule' : 작성 규칙(components/*.md 대응). 트리에서 개인/시스템을 나란히 노출한다.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError as _SpecValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.db.models.user import User
from src.db.models.user_prompt import UserPrompt
from src.prompts import AnalystSpec, VolumeTarget, list_analysts, load_component
from src.services.prompts.composer import compose_agent_prompt

VALID_KINDS = ("agent", "rule")


def _check_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise ValidationError(
            message=f"알 수 없는 프롬프트 종류: {kind} (가능: {', '.join(VALID_KINDS)})",
            code="INVALID_PROMPT_KIND",
        )


def _check_public(kind: str, is_public: bool) -> None:
    """공개는 에이전트만. 작성 규칙은 프로젝트가 id로 골라 붙이는 소유자 스코프
    계약이라(_validate_rules_config가 남의 id를 막는다) 공개해도 남이 쓸 길이 없다 —
    켜지는 스위치인데 아무 일도 안 일어나면 그게 거짓 스위치다.
    """
    if is_public and kind != "agent":
        raise ValidationError(
            message="공개는 분석 에이전트만 가능합니다", code="PROMPT_NOT_SHAREABLE"
        )


# 목표 분량은 숫자로 직접 받는다(천 단위). 3단 버튼으로는 시스템 21종의 실제 분포
# (11,250~60,000자)를 담을 수 없었다 — 특허분석 2만~6만, 산업연관 1.1만~1.8만처럼
# 종마다 다르다. 레거시 3단(spec.volume)은 예전에 저장된 값을 위해 남겨 둔다.
VOLUME_PRESETS: dict[str, VolumeTarget] = {
    "short": VolumeTarget(min_chars=8000, max_chars=12000, pages="5~8p"),
    "normal": VolumeTarget(min_chars=15000, max_chars=22500, pages="10~15p"),
    "long": VolumeTarget(min_chars=20000, max_chars=33750, pages="15~22p"),
}

# 사람이 넣을 수 있는 범위 — 아래로는 절 하나의 골격, 위로는 단일 절 현실 상한.
MIN_VOLUME_CHARS = 1000
MAX_VOLUME_CHARS = 60000
# 페이지 환산 계수(자/페이지). 시스템 카탈로그 값들(15000~22500 = 10~15p 등)에서 역산.
_CHARS_PER_PAGE = 1500

# 작성 규칙 슬롯 — 시스템 조각 3종의 고정 순서. 개인 규칙은 base_ref로 이 중
# 하나를 교체하거나(슬롯 교체), base_ref 없이 뒤에 덧붙는다(추가 규칙).
RULE_SLOTS: tuple[str, ...] = ("agent_source_rules", "agent_visual_rules", "agent_writing_style")


def pages_label(min_chars: int, max_chars: int) -> str:
    """분량 범위 → '10~15p' 표기. 카탈로그 표기와 같은 계수로 환산한다."""
    return f"{max(1, min_chars // _CHARS_PER_PAGE)}~{max(1, max_chars // _CHARS_PER_PAGE)}p"


def volume_from_spec(spec: dict | None) -> VolumeTarget | None:
    """spec → 목표 분량. 숫자(min_chars/max_chars)가 우선, 없으면 레거시 3단, 둘 다 없으면 None.

    None이면 호출부가 원본 승계(시스템 에이전트 덮어쓰기) 또는 기본값을 쓴다 —
    '지정하지 않음'과 '0으로 지정'을 구분해야 원본 분량이 조용히 깎이지 않는다.
    """
    if not isinstance(spec, dict):
        return None
    lo, hi = spec.get("min_chars"), spec.get("max_chars")
    if isinstance(lo, int) and isinstance(hi, int) and MIN_VOLUME_CHARS <= lo < hi:
        hi = min(hi, MAX_VOLUME_CHARS)
        return VolumeTarget(min_chars=lo, max_chars=hi, pages=pages_label(lo, hi))
    return VOLUME_PRESETS.get(str(spec.get("volume") or ""))


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


def _content_from(kind: str, name: str, content: str, spec: dict | None) -> str:
    """칸 값(spec.sections)이 오면 프롬프트를 조합해 쓴다 — 자유 편집이면 원문 그대로.

    조합 결과가 작성 경로의 단일 진실이다. 칸 값은 spec에 남아 재편집을 가능케 한다.
    """
    sections = (spec or {}).get("sections") if isinstance(spec, dict) else None
    if kind == "agent" and isinstance(sections, dict) and any(sections.values()):
        return compose_agent_prompt(name, sections)
    return content


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
    is_public: bool = False,
) -> UserPrompt:
    _check_kind(kind)
    _check_public(kind, is_public)
    content = _content_from(kind, name, content, spec)
    row = UserPrompt(
        owner_id=owner_id,
        kind=kind,
        name=name,
        content=content,
        base_ref=base_ref,
        cat=cat,
        description=description,
        spec=spec or {},
        is_public=is_public,
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
    is_public: bool | None = None,
) -> UserPrompt:
    """개인 프롬프트 수정 — kind·base_ref는 불변(오버라이드 대상은 생성 시 확정)."""
    row = await get_personal(session, owner_id, prompt_id)
    if is_public is not None:
        _check_public(row.kind, is_public)
        row.is_public = is_public
    if name is not None:
        row.name = name
    if content is not None:
        row.content = content
    if spec is not None and spec.get("sections"):
        # 칸을 고쳤으면 본문을 다시 조합한다(이름 변경도 제목 줄에 반영).
        row.content = _content_from(row.kind, name or row.name, row.content, spec)
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


async def list_public_agents(
    session: AsyncSession, viewer_id: UUID | None = None
) -> list[tuple[UserPrompt, str]]:
    """공개된 개인 에이전트 + 소유자 이름. viewer_id를 주면 그 사람 것은 뺀다.

    자기 것은 개인 층에서 이미 나오므로 공개 층에서 또 넣으면 목록에 두 벌 뜬다.
    """
    stmt = (
        select(UserPrompt, User.name)
        .join(User, User.id == UserPrompt.owner_id)
        .where(UserPrompt.kind == "agent", UserPrompt.is_public.is_(True))
    )
    if viewer_id is not None:
        stmt = stmt.where(UserPrompt.owner_id != viewer_id)
    stmt = stmt.order_by(UserPrompt.updated_at.desc())
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


def _unique_name(name: str, owner_name: str, prompt_id: UUID, taken: set[str]) -> str:
    """목록 안에서 유일한 표시 이름. 목차·프리셋이 에이전트를 **이름**으로 참조하므로
    (OutlineEditor의 칩이 a.name을 넣는다) 같은 이름이 둘이면 배정이 어느 쪽인지
    갈리지 않는다 — 겹칠 때만 소유자를 덧붙여 가른다. 안 겹치면 원래 이름 그대로라
    이미 저장된 목차의 참조가 깨지지 않는다.
    """
    if name not in taken:
        return name
    with_owner = f"{name} ({owner_name})"
    if with_owner not in taken:
        return with_owner
    return f"{name} ({owner_name}·{str(prompt_id)[:4]})"


def shared_spec(p: UserPrompt, owner_name: str, taken: set[str]) -> AnalystSpec:
    """공개된 남의 에이전트 → 카탈로그 항목. 개인 에이전트와 같은 id 규약(u-<uuid>)."""
    return AnalystSpec(
        id=f"u-{p.id}",
        name=_unique_name(p.name, owner_name, p.id, taken),
        cat=p.cat or "공유",
        desc=p.description or "",
        queries=[q for q in (p.spec or {}).get("queries", []) if isinstance(q, str)],
        prompt=p.content,
        volume_target=volume_from_spec(p.spec) or VOLUME_PRESETS["normal"],
        shared=True,
        owner_name=owner_name,
    )


async def resolve_analysts(session: AsyncSession, owner_id: UUID) -> list[AnalystSpec]:
    """시스템 → 내 개인 → 남의 공개 순으로 병합한 분석 에이전트 목록.

    base_ref가 시스템 에이전트(id/name)를 가리키는 개인 에이전트는 그 프롬프트를 덮어쓰고,
    base_ref 없는 개인 에이전트는 뒤에 새로 붙는다(id=`u-<uuid>`).

    남이 공개한 에이전트(is_public)는 **덮어쓰지 않고 뒤에 붙기만 한다** — 남의 오버라이드가
    내 시스템 에이전트를 조용히 바꾸면 같은 이름으로 다른 글이 나온다. base_ref가 달린
    공개 에이전트도 그 변형본 자체로 한 항목이 된다.
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
                        # 분량은 고쳐 적었으면 그 값, 아니면 원본 승계 — 폼의
                        # '원본 유지'가 spec.volume을 비워 보내 이 경로를 탄다.
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

    # 공개 층 — 내 것/시스템 이름을 밀어내지 않도록 마지막에, 이름 충돌만 가려서 붙인다.
    taken = {spec.name for spec in merged}
    for p, owner_name in await list_public_agents(session, owner_id):
        spec = shared_spec(p, owner_name, taken)
        taken.add(spec.name)
        merged.append(spec)
    return merged


async def import_public_agent(session: AsyncSession, owner_id: UUID, source_id: UUID) -> UserPrompt:
    """공개된 남의 에이전트를 내 것으로 복제한다.

    복사본은 **base_ref=None**으로 만든다 — 남이 시스템 에이전트를 덮어쓴 변형본이라도
    내 시스템 에이전트까지 조용히 갈아끼우면 안 된다(3층 병합에서 공개분을 덮어쓰기로
    쓰지 않는 것과 같은 원칙). 공개 여부도 승계하지 않는다 — 남의 것을 가져왔다고
    내 이름으로 자동 재공개되면 곤란하다.

    이름이 겹치면 "(사본)"을 붙인다(owner_id+kind+name 유니크).
    """
    src = await session.get(UserPrompt, source_id)
    if src is None or src.kind != "agent" or not (src.is_public or src.owner_id == owner_id):
        raise NotFoundError(message="가져올 에이전트를 찾을 수 없습니다", code="PROMPT_NOT_FOUND")
    taken = set(
        (
            await session.execute(
                select(UserPrompt.name).where(
                    UserPrompt.owner_id == owner_id, UserPrompt.kind == "agent"
                )
            )
        )
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
    row = UserPrompt(
        owner_id=owner_id,
        kind="agent",
        name=name,
        content=src.content,
        base_ref=None,
        cat=src.cat,
        description=src.description,
        spec=dict(src.spec or {}),
        is_public=False,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def snapshot_agents(session: AsyncSession, owner_id: UUID) -> list[dict]:
    """런 시작 시점의 DB 출신 에이전트를 얼려 둘 형태로 돌려준다.

    남의 공개 에이전트는 라이브 참조라(주인이 언제든 고치고 내린다) 런이 도는 도중
    페르소나가 바뀌거나 사라질 수 있다 — 재개(resume)까지 생각하면 같은 보고서의 절마다
    다른 프롬프트로 쓰이는 일이 생긴다. 시작 순간의 값을 config에 남겨 그 런 내내 같은
    글을 쓰게 한다(2026-08-19 사용자 결정: 라이브 참조 + 런 시작 스냅샷).

    파일 카탈로그와 프롬프트가 같은 항목은 담지 않는다 — 파일은 배포로만 바뀌고,
    전부 담으면 config에 24종 프롬프트가 통째로 들어앉는다.
    """
    file_prompts = {a.id: a.prompt for a in list_analysts()}
    return [
        spec.model_dump(mode="json")
        for spec in await resolve_analysts(session, owner_id)
        if file_prompts.get(spec.id) != spec.prompt
    ]


def specs_from_snapshot(raw: list) -> list[AnalystSpec]:
    """얼린 스냅샷 + 파일 카탈로그 → 그 런의 에이전트 목록.

    같은 id는 스냅샷이 이긴다(개인 오버라이드가 시스템 항목을 대체한 상태 그대로).
    형태가 깨진 항목은 조용히 버린다 — 옛 런의 config를 읽다 실행이 죽으면 안 된다.
    """
    frozen: dict[str, AnalystSpec] = {}
    for item in raw:
        try:
            spec = AnalystSpec.model_validate(item)
        except (_SpecValidationError, TypeError):
            continue
        frozen[spec.id] = spec
    merged = [frozen.pop(a.id, a) for a in list_analysts()]
    merged.extend(frozen.values())
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
