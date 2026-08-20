"""0043 절 정체성 수술 — outline 안정 id 백필 + 절 id 컬럼 2개.

목차를 한 글자만 고쳐도 전 절의 section_id가 재발급되던 구조(2026-08-21 진단)를
바꾸는 마이그레이션 몫:

1. projects.config.outline의 장·절에 안정 id(uuid)를 백필한다. 같은 절의 id는
   _section_plan 정본(위치 대응)에서, 없으면 sections 행(번호 대응)에서 가져와
   기존 계획·리허설 캐시·본문 행과의 연결이 그대로 살아 있게 한다.
2. builds_on의 번호 표기("4.1"·"4.1(지표)"·"4.*")를 id 토큰("s:<uuid>"·"c:<uuid>")
   으로 변환한다 — 이후 절 삽입·삭제가 참조를 어긋나게 하지 않는다.
3. verify_findings.section_id — 경고의 절 매칭·이동 정본(번호 문자열은 표시값으로).
4. section_rehearsals.plan_hash — id가 안정화되면 "목차가 바뀌면 id도 바뀐다"는
   캐시 무효화 가정이 깨지므로, 계획 내용 지문으로 대신 잡는다.

downgrade는 컬럼만 지운다. outline에 백필된 id·토큰은 남는다 — 옛 코드의 검증이
id 토큰을 못 읽어 목차 저장을 거부하므로, 되돌린 뒤 목차를 고치려면 builds_on을
번호 표기로 손봐야 한다(비상 롤백 전용).

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-21
"""

import json
import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# core/builds_on._REF_RE와 같은 문법 — 마이그레이션은 src와 독립(자체 완결)이어야
# 나중에 src가 바뀌어도 과거 리비전이 그대로 재생된다.
_REF_RE = re.compile(
    r"^\s*(?P<chapter>\d+)\.(?P<section>\d+|\*)(?:\(\s*(?P<metric>[^()]+?)\s*\))?\s*$"
)


def _to_token(
    raw: str, sec_by_pos: dict[tuple[int, int], str], ch_by_num: dict[int, str]
) -> str:
    """번호 표기 → id 토큰. 해석 못 하면 원문 유지(하류가 위치 해석 폴백)."""
    m = _REF_RE.match(raw)
    if m is None:
        return raw
    chapter = int(m.group("chapter"))
    if m.group("section") == "*":
        ch_id = ch_by_num.get(chapter)
        return f"c:{ch_id}" if ch_id else raw
    target = sec_by_pos.get((chapter, int(m.group("section"))))
    if target is None:
        return raw
    metric = (m.group("metric") or "").strip()
    return f"s:{target}({metric})" if metric else f"s:{target}"


def _backfill_outline(config: dict, row_id_by_pos: dict[tuple[int, int], str]) -> dict | None:
    """outline에 id·토큰을 채운 새 config. 바꿀 게 없으면 None."""
    outline = config.get("outline")
    if not isinstance(outline, dict) or not isinstance(outline.get("chapters"), list):
        return None

    # 절 id 출처 우선순위: _section_plan 정본(위치 순서 그대로 생성됨) → sections 행
    # (번호 대응) → 신규 발급. plan과 outline은 같은 순회로 만들어져 위치가 동기다.
    plan_ids: dict[tuple[int, int], str] = {}
    for item in config.get("_section_plan") or []:
        if not isinstance(item, dict):
            continue
        try:
            pos = (int(item["chapter_number"]), int(item["section_number"]))
            plan_ids[pos] = str(uuid.UUID(str(item["section_id"])))
        except (KeyError, ValueError, TypeError):
            continue

    changed = False
    seen: set[str] = set()
    chapters_out = []
    sec_by_pos: dict[tuple[int, int], str] = {}
    ch_by_num: dict[int, str] = {}
    for ci, chapter in enumerate(outline["chapters"], start=1):
        if not isinstance(chapter, dict):
            chapters_out.append(chapter)
            continue
        ch = dict(chapter)
        if not _valid(ch.get("id"), seen):
            ch["id"] = str(uuid.uuid4())
            changed = True
        seen.add(str(ch["id"]))
        ch_by_num[ci] = str(ch["id"])
        sections_out = []
        for si, sec in enumerate(ch.get("sections") or [], start=1):
            if not isinstance(sec, dict):
                sections_out.append(sec)
                continue
            s = dict(sec)
            if not _valid(s.get("id"), seen):
                s["id"] = (
                    plan_ids.get((ci, si))
                    or row_id_by_pos.get((ci, si))
                    or str(uuid.uuid4())
                )
                if s["id"] in seen:
                    s["id"] = str(uuid.uuid4())
                changed = True
            seen.add(str(s["id"]))
            sec_by_pos[(ci, si)] = str(s["id"])
            sections_out.append(s)
        ch["sections"] = sections_out
        chapters_out.append(ch)

    for ch in chapters_out:
        if not isinstance(ch, dict):
            continue
        for s in ch.get("sections") or []:
            if not isinstance(s, dict) or not isinstance(s.get("builds_on"), list):
                continue
            converted = [
                _to_token(b, sec_by_pos, ch_by_num) if isinstance(b, str) else b
                for b in s["builds_on"]
            ]
            if converted != s["builds_on"]:
                s["builds_on"] = converted
                changed = True

    if not changed:
        return None
    return {**config, "outline": {**outline, "chapters": chapters_out}}


def _valid(raw, seen: set[str]) -> bool:
    try:
        return str(uuid.UUID(str(raw))) == str(raw) and str(raw) not in seen
    except (ValueError, TypeError, AttributeError):
        return False


def upgrade() -> None:
    op.add_column(
        "verify_findings",
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "section_rehearsals",
        sa.Column("plan_hash", sa.String(length=40), nullable=False, server_default=""),
    )

    bind = op.get_bind()
    projects = bind.execute(sa.text("SELECT id, config FROM projects")).fetchall()
    section_rows = bind.execute(
        sa.text("SELECT id, project_id, chapter_number, section_number FROM sections")
    ).fetchall()
    rows_by_project: dict[str, dict[tuple[int, int], str]] = {}
    for row in section_rows:
        rows_by_project.setdefault(str(row.project_id), {})[
            (row.chapter_number, row.section_number)
        ] = str(row.id)

    for row in projects:
        config = row.config if isinstance(row.config, dict) else None
        if not config:
            continue
        updated = _backfill_outline(config, rows_by_project.get(str(row.id), {}))
        if updated is None:
            continue
        bind.execute(
            sa.text("UPDATE projects SET config = CAST(:cfg AS jsonb) WHERE id = :id"),
            {"cfg": json.dumps(updated, ensure_ascii=False), "id": str(row.id)},
        )


def downgrade() -> None:
    op.drop_column("section_rehearsals", "plan_hash")
    op.drop_column("verify_findings", "section_id")
