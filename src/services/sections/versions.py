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
from sqlalchemy import delete, func, select
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


# 버전 보존 — 스냅샷 한 벌이 **보고서 전체**라 잦은 수정이 그대로 쌓인다.
# 실측(2026-08-27) 한 벌 최대 415 kB(예타 35절 377,865자). 블록 수정 100번이면
# 그 프로젝트 하나가 41 MB다.
#
# 그래서 둘로 가른다. **이정표**(조립·확정·다시열기·목차·자료·수동·원본)는 문서의
# 마디라 전부 남기고, **잦은 수정**(절·블록 재작성, 손편집, 되돌리기)은 최근 것만
# 남긴다. 되돌릴 대상은 거의 언제나 직전 몇 개이고, 더 옛것으로 가려는 사람은
# 이정표를 짚는다.
_CHURN_PREFIXES = ("rewrite:", "block:", "edit:", "restore:")
KEEP_RECENT_CHURN = 20


async def prune_versions(
    session: AsyncSession, project_id: UUID, *, keep: int = KEEP_RECENT_CHURN
) -> int:
    """잦은 수정 버전을 최근 `keep`개만 남기고 지운다 → 지운 개수.

    이정표는 몇 개든 손대지 않는다. 번호(version_no)는 최대값+1로 매기므로 지워도
    다시 쓰이지 않는다 - 남은 번호가 듬성해질 뿐 가리키는 대상은 안 바뀐다.
    """
    rows = (
        await session.execute(
            select(ReportVersion.id, ReportVersion.reason)
            .where(ReportVersion.project_id == project_id)
            .order_by(ReportVersion.version_no.desc())
        )
    ).all()
    churn = [r for r in rows if str(r.reason or "").startswith(_CHURN_PREFIXES)]
    doomed = [r.id for r in churn[keep:]]
    if not doomed:
        return 0
    await session.execute(delete(ReportVersion).where(ReportVersion.id.in_(doomed)))
    logger.info(
        "report_version.pruned",
        project_id=str(project_id),
        n_pruned=len(doomed),
        kept_churn=keep,
    )
    return len(doomed)


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
    # 새 벌을 담은 **직후**에 정리한다 - 방금 것이 최근 목록에 들어간 상태로 세야
    # "최근 20개"가 늘 방금 것을 포함한다.
    await prune_versions(session, project_id)
    return next_no


async def ensure_baseline_version(
    session: AsyncSession,
    project_id: UUID,
    *,
    created_by: UUID | None = None,
) -> int | None:
    """덮어쓰기 **직전**에 원본을 한 번 얼린다 - 버전이 하나도 없는 문서를 위해.

    재작성·편집·블록 수정은 모두 '성공 직후'를 얼린다. 거기엔 "고치기 전은 직전 버전이
    이미 들고 있다"는 전제가 깔려 있는데, 그 전제가 **안 맞는 문서가 실제로 있었다**:
    버전 기록이 붙기 전에 만들어진 문서, 조립 스냅샷이 남지 않은 문서(2026-08-27 실측
    18건 중 10건). 그런 문서는 첫 수정이 원본을 통째로 지우고, 화면은 그동안
    "이전 본문은 버전 기록에 남아 있습니다"라고 말하고 있었다.

    이미 버전이 하나라도 있으면 아무것도 하지 않는다(직전 버전이 원본이다).
    """
    existing = (
        await session.execute(
            select(ReportVersion.id).where(ReportVersion.project_id == project_id).limit(1)
        )
    ).first()
    if existing is not None:
        return None
    return await snapshot_report(session, project_id, reason="baseline", created_by=created_by)


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


async def sections_fingerprint(session: AsyncSession, project_id: UUID) -> str:
    """현재 본문의 지문 — "이 판정이 어느 본문에 대한 것이었나"를 나중에 대조하려고.

    버전 중복 판정과 **같은 지문**을 쓴다(절 id·번호·제목·본문). PM 검증처럼 본문을
    통째로 보고 판정하는 절차는 이 값을 찍어 두면, 뒤에 사람이 절을 고쳤을 때
    "그 판정은 지금 본문에 대한 게 아니다"를 화면이 말할 수 있다(2026-08-27).
    """
    return _content_hash(await current_sections_snapshot(session, project_id))


def section_timeline(
    versions: list[tuple[int, str, Any, Any]], section_id: UUID
) -> list[dict[str, Any]]:
    """버전 목록 → **이 절 하나**의 이력. 같은 내용이 이어지는 구간은 하나로 접는다.

    versions는 (version_no, reason, created_at, sections JSONB)를 **오름차순**으로 받는다.

    접는 이유: 버전은 어느 절을 고쳐도 하나씩 생긴다(절 20개 보고서에서 3.2를 한 번
    고치면 나머지 19절의 이력에도 같은 내용이 한 줄씩 늘어난다). 접지 않으면 이력이
    "안 바뀐 기록"으로 가득 찬다 — 사람이 보려는 건 이 절이 **달라진 시점**뿐이다.

    각 항목의 version_no는 그 내용이 **처음 나타난** 버전이고, until_version은 같은
    내용이 유지된 마지막 버전이다(어느 쪽에서 되돌려도 결과가 같다).
    """
    out: list[dict[str, Any]] = []
    for version_no, reason, created_at, sections in versions:
        snap = next(
            (s for s in (sections or []) if str(s.get("section_id")) == str(section_id)), None
        )
        if snap is None:
            continue  # 그 시점 목차에 없던 절(나중에 추가됨)
        content = str(snap.get("content") or "")
        if out and out[-1]["content"] == content and out[-1]["title"] == snap.get("title"):
            out[-1]["until_version"] = version_no
            out[-1]["until_at"] = created_at
            continue
        out.append(
            {
                "version_no": version_no,
                "until_version": version_no,
                "reason": reason,
                "created_at": created_at,
                "until_at": created_at,
                "title": str(snap.get("title") or ""),
                "chapter_number": int(snap.get("chapter_number") or 0),
                "section_number": int(snap.get("section_number") or 0),
                "content": content,
                "char_count": len(content),
            }
        )
    return out


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


async def restore_section(
    session: AsyncSession, project_id: UUID, version_no: int, section_id: UUID
) -> dict[str, Any] | None:
    """한 절만 그 버전의 내용으로 되돌린다 — 없으면 None.

    **전체 롤백이 아니라 절 단위인 이유**: 보고서는 여러 번에 걸쳐 고쳐진다. 3.2가
    마음에 안 들어 되돌리려는데 전체를 롤백하면 그 사이 손본 다른 절의 개선까지 함께
    사라진다. 실제로 사람이 원하는 건 "이 절만 그때로"다(2026-08-26).

    되돌린 직후를 다시 스냅샷하지 않는다 — 호출부가 그 책임을 진다(되돌리기도 덮어
    쓰기라 흔적이 남아야 하지만, 커밋 경계는 opener가 정한다).

    plan_hash는 **건드리지 않는다**: 되돌린 본문이 지금 계획대로인지 아닌지는 지문이
    아니라 그 시점 계약이 답한다. 옛 본문을 되살리면 미반영으로 뜨는 게 정직하다.
    """
    row = (
        await session.execute(
            select(ReportVersion).where(
                ReportVersion.project_id == project_id,
                ReportVersion.version_no == version_no,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    snap = next(
        (s for s in (row.sections or []) if str(s.get("section_id")) == str(section_id)), None
    )
    if snap is None:
        return None

    target = (
        await session.execute(
            select(Section).where(Section.project_id == project_id, Section.id == section_id)
        )
    ).scalar_one_or_none()
    if target is None:
        return None

    target.content = str(snap.get("content") or "")
    target.source_ids = [UUID(str(s)) for s in (snap.get("source_ids") or [])]
    target.status = "completed" if target.content.strip() else "pending"
    logger.info(
        "section.restored",
        project_id=str(project_id),
        section_id=str(section_id),
        from_version=version_no,
    )
    return snap
