"""인용 전역 번호화 — 절-로컬 [n]을 문서 공통(출처장) 번호로 재매핑.

작성기는 절마다 검색 풀 인덱스로 [1..k]를 매긴다(절-로컬). 그대로 두면 본문 번호와
출처 최종장 번호가 어긋나 독자가 검증할 수 없다(2026-08-05 지적). 조립 시점에
결정적으로 재작성한다:

- 전역 번호 = 채택(is_included) 자료의 수집 순서(created_at) — 자료 검토 목록·
  출처 최종장과 동일한 순서라, 이후 편집·재렌더에도 흔들리지 않는다.
- 로컬 [n]의 의미는 작성 규약(candidates._extract_cited_ids)에서 복원한다:
  본문에 등장한 서로 다른 n의 첫 등장 순서 = cited_chunk_ids 저장 순서.
- 매핑이 안 되는 마커(범위 밖 환각, 요약 청크)는 제거한다(로컬 번호를 남기면 오독).
"""

from __future__ import annotations

import re
from uuid import UUID

import structlog

from src.core.state import ProjectState

logger = structlog.get_logger(__name__)

_CITE_RE = re.compile(r"\[(\d+)\]")


def renumber_content(
    content: str,
    cited_chunk_ids: list[UUID],
    chunk_to_global: dict[UUID, int],
) -> str:
    """본문 [로컬n] → [전역g] 재작성 (순수 함수).

    로컬 n(등장 순서 i번째 고유 번호) ↔ cited_chunk_ids[i] ↔ 전역 번호.
    매핑 없는 마커는 앞 공백과 함께 제거한다.
    """
    order: list[int] = []
    for m in _CITE_RE.finditer(content):
        n = int(m.group(1))
        if n not in order:
            order.append(n)
    local_to_global: dict[int, int] = {}
    for i, n in enumerate(order):
        if i < len(cited_chunk_ids):
            g = chunk_to_global.get(cited_chunk_ids[i])
            if g is not None:
                local_to_global[n] = g

    def _sub(m: re.Match[str]) -> str:
        g = local_to_global.get(int(m.group(2)))
        if g is None:
            # 매핑 없는 마커는 앞 공백(개행 제외)과 함께 제거 — 흔적을 남기지 않는다.
            return ""
        return f"{m.group(1)}[{g}]"  # 원래 간격 보존

    return re.sub(r"([ \t]*)\[(\d+)\]", _sub, content)


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
    order: list[int] = []
    for m in _CITE_RE.finditer(content):
        n = int(m.group(1))
        if n not in order:
            order.append(n)
    out: dict[int, list[UUID]] = {}
    for i, _n in enumerate(order):
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
                new_draft = cand.draft.model_copy(
                    update={"content": renumber_content(cand.draft.content, cited, chunk_to_global)}
                )
                cand = cand.model_copy(update={"draft": new_draft})
            new_cands.append(cand)
        new_sets.append(cset.model_copy(update={"candidates": new_cands}))
    logger.info(
        "citations.renumbered",
        project_id=str(state.project_id),
        n_chunks=len(chunk_to_global),
    )
    return state.model_copy(update={"section_candidates": new_sets}).with_section_meta(meta)
