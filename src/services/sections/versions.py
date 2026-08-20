"""보고서 버전 스냅샷·비교 — sections의 유일한 보존 지점 (2026-08-21 설계).

스냅샷 시점은 자동 2곳이다:
- assemble 완성 직후(reason="assemble") — "완성본마다 1버전".
- 재개(reopen) 직전(reason="reopen") — 완료 후 수동 편집분까지 보존하는 마지막 기회.
내용 지문(sha1)이 같으면 새 버전을 만들지 않는다(조립 직후 곧바로 재개하는 흐름에서
같은 내용이 두 번 얼리는 것을 막는다). 수동 버튼(reason="manual")은 2차.

비교(diff)는 저장하지 않고 조회 시 계산한다. 절 매칭은 **절 안정 id**가 정본이라
(정체성 수술 2026-08-21) 번호가 밀려도 "수정"과 "이동"을 오판하지 않는다. 문단
내부의 단어 단위 색칠은 프론트 몫 — 서버는 절 단위 판정과 양쪽 본문만 내려준다.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.report_version import ReportVersion
from src.db.models.section import Section

logger = structlog.get_logger(__name__)


def _snapshot_sections(rows: list[Section]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda r: (r.chapter_number, r.section_number))
    return [
        {
            "section_id": str(r.id),
            "chapter_number": r.chapter_number,
            "section_number": r.section_number,
            "chapter_title": r.chapter_title,
            "title": r.title,
            "content": r.content or "",
            "source_ids": [str(s) for s in (r.source_ids or [])],
        }
        for r in ordered
    ]


def _content_hash(sections: list[dict[str, Any]]) -> str:
    """절 id·본문·제목·순서의 지문 — 번호만 밀린 것도 다른 내용으로 본다(문서가 다르다)."""
    h = hashlib.sha1()
    for s in sections:
        h.update(
            f"{s['section_id']}|{s['chapter_number']}.{s['section_number']}|{s['title']}\n".encode()
        )
        h.update(s["content"].encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


async def snapshot_report(
    session: AsyncSession,
    project_id: UUID,
    *,
    reason: str,
    created_by: UUID | None = None,
) -> int | None:
    """현재 sections를 버전으로 얼린다 → 버전 번호. 스냅샷할 게 없으면 None.

    본문 있는 절이 하나도 없으면 만들지 않는다(빈 문서는 버전이 아니다). 마지막
    버전과 내용 지문이 같아도 만들지 않고 그 번호를 돌려준다(중복 방지).
    """
    rows = (
        (await session.execute(select(Section).where(Section.project_id == project_id)))
        .scalars()
        .all()
    )
    sections = _snapshot_sections(list(rows))
    if not any(s["content"].strip() for s in sections):
        return None
    digest = _content_hash(sections)
    latest = (
        await session.execute(
            select(ReportVersion)
            .where(ReportVersion.project_id == project_id)
            .order_by(ReportVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None and latest.content_hash == digest:
        return latest.version_no
    next_no = (latest.version_no if latest is not None else 0) + 1
    session.add(
        ReportVersion(
            project_id=project_id,
            version_no=next_no,
            reason=reason,
            sections=sections,
            content_hash=digest,
            created_by=created_by,
        )
    )
    await session.flush()
    logger.info(
        "report_version.snapshot",
        project_id=str(project_id),
        version_no=next_no,
        reason=reason,
        n_sections=len(sections),
    )
    return next_no


async def snapshot_report_standalone(project_id: UUID, *, reason: str) -> int | None:
    """요청 밖(assemble 단계) 경로 — 자체 세션을 열고 직접 커밋한다(session.py 규칙)."""
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        version_no = await snapshot_report(session, project_id, reason=reason)
        await session.commit()
    return version_no


async def current_sections_snapshot(
    session: AsyncSession, project_id: UUID
) -> list[dict[str, Any]]:
    """현재 sections 행 → 스냅샷과 같은 모양. 버전↔현재 비교(diff target=현재)용."""
    rows = (
        (await session.execute(select(Section).where(Section.project_id == project_id)))
        .scalars()
        .all()
    )
    return _snapshot_sections(list(rows))


async def latest_version_no(session: AsyncSession, project_id: UUID) -> int:
    n = (
        await session.execute(
            select(func.max(ReportVersion.version_no)).where(ReportVersion.project_id == project_id)
        )
    ).scalar_one_or_none()
    return int(n or 0)


def diff_sections(base: list[dict[str, Any]], target: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """두 스냅샷의 절 단위 비교 — 절 안정 id로 맞춘다(순수 함수).

    항목: {section_id, status: added|removed|modified|unchanged, moved: bool,
    base: {...}|None, target: {...}|None}. 순서는 target 순서를 따르고, 삭제된
    절은 base에서의 앞 절 뒤에 끼워 문서 흐름을 유지한다(GitHub의 deleted file이
    트리에 남는 것과 같은 원칙 — 지워진 것도 보여야 비교다).
    """
    base_by_id = {s["section_id"]: s for s in base}
    target_ids = {s["section_id"] for s in target}
    out: list[dict[str, Any]] = []

    def entry(s_base: dict | None, s_target: dict | None) -> dict[str, Any]:
        if s_base is None:
            return {
                "section_id": s_target["section_id"],
                "status": "added",
                "moved": False,
                "base": None,
                "target": s_target,
            }
        if s_target is None:
            return {
                "section_id": s_base["section_id"],
                "status": "removed",
                "moved": False,
                "base": s_base,
                "target": None,
            }
        modified = s_base["content"] != s_target["content"] or s_base["title"] != s_target["title"]
        moved = (s_base["chapter_number"], s_base["section_number"]) != (
            s_target["chapter_number"],
            s_target["section_number"],
        )
        return {
            "section_id": s_base["section_id"],
            "status": "modified" if modified else "unchanged",
            "moved": moved,
            "base": s_base,
            "target": s_target,
        }

    # base 순서에서 "이 절 뒤에 지워진 절들" 지도를 만든다.
    removed_after: dict[str | None, list[dict]] = {}
    prev_alive: str | None = None
    for s in base:
        if s["section_id"] in target_ids:
            prev_alive = s["section_id"]
        else:
            removed_after.setdefault(prev_alive, []).append(s)

    for s in removed_after.get(None, []):
        out.append(entry(s, None))
    for s in target:
        out.append(entry(base_by_id.get(s["section_id"]), s))
        for gone in removed_after.get(s["section_id"], []):
            out.append(entry(gone, None))
    # base에서 이어지던 자리(앞 절)가 target에서도 지워진 경우 — 위 루프가 못 끼운
    # 잔여분을 끝에 붙인다(유실 방지).
    emitted = {e["section_id"] for e in out}
    for s in base:
        if s["section_id"] not in emitted:
            out.append(entry(s, None))
    return out
