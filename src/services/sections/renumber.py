"""인용 전역 번호화 — 절-로컬 번호를 문서 공통(출처장) 번호로 재매핑.

작성기는 절마다 검색 풀 인덱스로 1..k를 매긴다(절-로컬). 그대로 두면 본문 번호와
출처 최종장 번호가 어긋나 독자가 검증할 수 없다(2026-08-05 지적). 조립 시점에
결정적으로 재작성한다:

- 전역 번호 = 채택(is_included) 자료의 수집 순서(created_at) — 자료 검토 목록·
  출처 최종장과 동일한 순서라, 이후 편집·재렌더에도 흔들리지 않는다.
- 로컬 번호의 의미는 작성 규약(candidates._extract_cited_ids)에서 복원한다:
  본문에 등장한 서로 다른 번호의 첫 등장 순서 = cited_chunk_ids 저장 순서.
- 매핑이 안 되는 마커(범위 밖 환각, 요약 청크)는 제거한다(로컬 번호를 남기면 오독).

표기 문법((출처 n)·[n])은 src/core/citations.py가 단일 진실이다 — 이 모듈은 번호
체계만 다룬다.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.core.citations import numbers_in_order
from src.core.citations import renumber as renumber_marks
from src.core.state import ProjectState
from src.services.sections.scrub import scrub_leftovers

logger = structlog.get_logger(__name__)


def _local_to_global(
    content: str,
    cited_chunk_ids: list[UUID],
    chunk_to_global: dict[UUID, int],
) -> dict[int, int]:
    """로컬 번호 → 전역 번호. 로컬 n의 첫 등장 순서가 cited_chunk_ids 순서라는 규약을 편다."""
    mapping: dict[int, int] = {}
    for i, n in enumerate(numbers_in_order(content)):
        if i >= len(cited_chunk_ids):
            break
        g = chunk_to_global.get(cited_chunk_ids[i])
        if g is not None:
            mapping[n] = g
    return mapping


def renumber_content(
    content: str,
    cited_chunk_ids: list[UUID],
    chunk_to_global: dict[UUID, int],
) -> str:
    """본문 로컬 번호 → 전역 번호 재작성 (순수 함수).

    로컬 n(등장 순서 i번째 고유 번호) ↔ cited_chunk_ids[i] ↔ 전역 번호.
    표기 형태((출처 n)·[n])는 그대로 두고 번호만 바꾸며, 매핑 없는 마커는 제거한다.
    """
    return renumber_marks(content, _local_to_global(content, cited_chunk_ids, chunk_to_global))


def citation_chunk_map(
    content: str,
    cited_chunk_ids: list[UUID],
    chunk_to_global: dict[UUID, int],
) -> dict[int, list[UUID]]:
    """전역 번호 → 그 번호가 실제로 가리킨 청크들 (순수 함수).

    전역 번호는 자료(source) 단위라 한 자료의 서로 다른 청크가 같은 번호로 합쳐진다.
    그러면 재작성된 본문만 봐서는 "이 문장이 원문 어느 대목에서 나왔는지"를 되짚을 수
    없다(로컬 번호는 사라지고, 못 푼 마커는 삭제된다). 재작성과 같은 자리에서 매핑을
    떠 절 meta에 남긴다 — 이게 근거 추적의 유일한 원천이다.
    """
    out: dict[int, list[UUID]] = {}
    for i, _n in enumerate(numbers_in_order(content)):
        if i >= len(cited_chunk_ids):
            break
        cid = cited_chunk_ids[i]
        g = chunk_to_global.get(cid)
        if g is None:
            continue
        bucket = out.setdefault(g, [])
        if cid not in bucket:
            bucket.append(cid)
    return out


async def _adopted_source_order(project_id: UUID) -> dict[UUID, int]:
    """채택 자료 source_id → 전역 번호(1부터, created_at 순)."""
    from sqlalchemy import select

    from src.db.models.project_source import ProjectSource
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(ProjectSource.id)
                    .where(
                        ProjectSource.project_id == project_id,
                        ProjectSource.is_included.is_(True),
                    )
                    .order_by(ProjectSource.created_at)
                )
            )
            .scalars()
            .all()
        )
    return {sid: i for i, sid in enumerate(rows, start=1)}


async def _chunk_source_map(chunk_ids: set[UUID]) -> dict[UUID, UUID]:
    """인용 청크 id → 원본 자료 id (source 없는 청크는 제외)."""
    if not chunk_ids:
        return {}
    from sqlalchemy import select

    from src.db.models.chunk import Chunk
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            await session.execute(select(Chunk.id, Chunk.source_id).where(Chunk.id.in_(chunk_ids)))
        ).all()
    return {cid: sid for cid, sid in rows if sid is not None}


async def build_chunk_to_global(project_id: UUID, chunk_ids: set[UUID]) -> dict[UUID, int]:
    """청크 id → 전역 출처 번호 매핑(조립·재작성 공용)."""
    source_order = await _adopted_source_order(project_id)
    chunk_source = await _chunk_source_map(chunk_ids)
    return {cid: source_order[sid] for cid, sid in chunk_source.items() if sid in source_order}


async def renumber_state(state: ProjectState) -> ProjectState:
    """선택 확정 초안 전체를 전역 번호로 재작성한 state를 돌려준다(조립 진입점)."""
    drafts = state.selected_drafts()
    all_chunk_ids = {cid for d in drafts for cid in d.cited_chunk_ids}
    chunk_to_global = await build_chunk_to_global(state.project_id, all_chunk_ids)
    if not chunk_to_global:
        return state

    new_sets = []
    meta: dict[UUID, dict] = {}
    for cset in state.section_candidates:
        chosen = state.section_selections.get(cset.section_id)
        new_cands = []
        for cand in cset.candidates:
            if cand.candidate_id == chosen:
                cited = list(cand.draft.cited_chunk_ids)
                # 매핑은 재작성 전 본문(로컬 번호) 기준으로만 뜰 수 있다 — 순서 주의.
                mapping = citation_chunk_map(cand.draft.content, cited, chunk_to_global)
                prior = dict(state.section_meta.get(cset.section_id) or {})
                prior["citation_chunks"] = {
                    str(g): [str(c) for c in cids] for g, cids in sorted(mapping.items())
                }
                meta[cset.section_id] = prior
                renumbered = renumber_content(cand.draft.content, cited, chunk_to_global)
                # 조립은 본문이 최종 확정되는 단일 지점 — 여기서 작성 잔재를 결정적으로
                # 세정한다(출처 배정 메모·오염 마커·기형 callout·'본 파트').
                # 검출만 하면 사람이 지워야 한다(2026-08-15 검증런: 메모 13건+ 노출).
                cleaned, scrub_notes = scrub_leftovers(renumbered)
                if scrub_notes:
                    logger.info(
                        "assemble.scrubbed",
                        project_id=str(state.project_id),
                        section_id=str(cset.section_id),
                        notes=scrub_notes,
                    )
                new_draft = cand.draft.model_copy(update={"content": cleaned})
                cand = cand.model_copy(update={"draft": new_draft})
            new_cands.append(cand)
        new_sets.append(cset.model_copy(update={"candidates": new_cands}))
    logger.info(
        "citations.renumbered",
        project_id=str(state.project_id),
        n_chunks=len(chunk_to_global),
    )
    return state.model_copy(update={"section_candidates": new_sets}).with_section_meta(meta)


def lost_evidence_paragraphs(before: str, after: str) -> list[dict[str, object]]:
    """마커가 지워진 문단들 — [{"text": 새 문단, "n_markers": 잃은 개수}].

    **왜 문단인가**: 자료를 빼면 그 자료를 가리키던 마커가 본문에서 사라진다(위 계약).
    그 자리의 문장은 주장을 그대로 하면서 근거만 잃는다. 절 전체를 다시 쓰면 실측
    $0.4~$1.3인데, 근거를 잃은 문단만 고치면 블록 재작성 1콜로 끝난다 — 십수 배 싸다.
    화면이 "어디를 고치면 되는지" 짚어 주려면 그 자리를 지금 기록해 둬야 한다. 나중에는
    알 수 없다: 지워진 마커는 흔적을 남기지 않는다.

    문단은 화면의 블록 나눔(빈 줄 기준)과 같은 단위다. 마커만 빠지므로 문단 수와 순서는
    그대로라 자리끼리 맞대면 된다 — 어긋나면(다른 편집이 겹쳤다) 아무것도 기록하지
    않는다. 틀린 자리를 짚느니 안 짚는 게 낫다.
    """
    sep = "\n\n"  # 빈 줄 = 화면의 블록 경계
    old_paras = before.split(sep)
    new_paras = after.split(sep)
    if len(old_paras) != len(new_paras):
        return []
    out: list[dict[str, object]] = []
    for old, new in zip(old_paras, new_paras, strict=True):
        lost = len(numbers_in_order(old)) - len(numbers_in_order(new))
        if lost > 0 and new.strip():
            out.append({"text": new.strip(), "n_markers": lost})
    return out


async def rebase_global_numbers(session, project_id: UUID) -> int:
    """저장된 본문의 전역 인용 번호를 **현재 채택 순서**에 다시 맞춘다.

    전역 번호는 채택(is_included) 자료의 수집 순서라, 자료를 채택에서 빼면 그 뒤
    번호가 통째로 당겨진다. 본문 마커는 그대로 남으므로 **문서 전체 인용이 한 칸씩
    어긋난다** — 참고문헌은 다운로드 시점에 다시 매겨지는데 본문은 안 매겨져서다.
    완료 보고서에서 자료를 뺄 수 있게 되면서(2026-08-26) 실제로 도달 가능해진 경로다.

    renumber_content(로컬→전역)와 다르다: 저장된 본문은 **이미 전역**이라 그 함수를
    다시 돌리면 전역 번호를 로컬로 오독해 망가진다. 여기서는 meta["citation_chunks"]
    (전역 번호 → 그 번호가 가리킨 청크들)를 열쇠로 **전역→전역** 재매핑을 한다.
    그래서 몇 번을 돌려도 결과가 같다.

    제외된 자료를 가리키던 마커는 매핑이 없어 사라진다(core.citations.renumber 계약)
    — 뺀 자료를 계속 인용하는 것보다 낫다.

    돌려주는 값은 실제로 본문이 바뀐 절 수.
    """
    from sqlalchemy import select

    from src.db.models.chunk import Chunk
    from src.db.models.project_source import ProjectSource
    from src.db.models.section import Section

    rows = (
        (await session.execute(select(Section).where(Section.project_id == project_id)))
        .scalars()
        .all()
    )
    marked = [(r, (r.meta or {}).get("citation_chunks") or {}) for r in rows]
    all_chunks = {UUID(c) for _r, m in marked for cids in m.values() for c in cids}
    if not all_chunks:
        return 0
    # 전역 번호는 **주입된 세션으로** 센다 — build_chunk_to_global은 자체 세션을 열어
    # 같은 트랜잭션의 미커밋 변경(방금 뒤집은 is_included)을 못 본다(2026-08-26 실측:
    # 제외 직후 호출인데 번호가 그대로였다).
    order = {
        sid: i
        for i, (sid,) in enumerate(
            (
                await session.execute(
                    select(ProjectSource.id)
                    .where(
                        ProjectSource.project_id == project_id,
                        ProjectSource.is_included.is_(True),
                    )
                    .order_by(ProjectSource.created_at)
                )
            ).all(),
            start=1,
        )
    }
    chunk_source = dict(
        (
            await session.execute(select(Chunk.id, Chunk.source_id).where(Chunk.id.in_(all_chunks)))
        ).all()
    )
    chunk_to_global = {cid: order[sid] for cid, sid in chunk_source.items() if sid in order}

    changed = 0
    for row, cmap in marked:
        if not cmap:
            continue
        old_to_new: dict[int, int] = {}
        new_cmap: dict[str, list[str]] = {}
        for old_g, cids in cmap.items():
            new_g = next(
                (chunk_to_global[UUID(c)] for c in cids if UUID(c) in chunk_to_global), None
            )
            if new_g is None:  # 그 자료가 채택에서 빠졌다 — 마커를 지운다
                continue
            old_to_new[int(old_g)] = new_g
            new_cmap[str(new_g)] = list(dict.fromkeys(cids + new_cmap.get(str(new_g), [])))
        if old_to_new == {int(k): int(k) for k in cmap}:
            continue  # 번호가 그대로다
        content = renumber_marks(row.content or "", old_to_new)
        if content == (row.content or ""):
            continue
        lost = lost_evidence_paragraphs(row.content or "", content)
        meta = {**(row.meta or {}), "citation_chunks": new_cmap}
        if lost:
            # 앞서 잃은 자리와 합친다 — 두 번째 제외가 첫 번째 기록을 지우면 안 된다.
            prev = [p for p in (meta.get("evidence_lost") or []) if isinstance(p, dict)]
            seen = {str(p.get("text")) for p in prev}
            meta["evidence_lost"] = prev + [p for p in lost if str(p["text"]) not in seen]
        row.content = content
        row.meta = meta
        changed += 1
    if changed:
        logger.info("citations.rebased", project_id=str(project_id), n_sections=changed)
    return changed
