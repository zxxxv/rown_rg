"""이 자료를 빼면 무엇이 무너지나 — 제외 버튼을 누르기 **전에** 답한다.

자료 제외는 조용한 파괴다. 누르는 순간 인용 번호가 다시 매겨지고, 그 자료를 근거로 쓴
절은 근거를 잃은 채 본문만 남는다. 지금까지 화면은 그 사실을 **누른 뒤에야**(개요의
미반영 배지로) 알려 줬다. 되돌리려면 다시 채택하고 그 절들을 다시 쓰는 수밖에 없는데,
그건 절당 실측 $0.4~$1.3짜리 되돌리기다.

여기서는 세 가지를 미리 센다:
- 몇 개 절이 이 자료를 인용했나 (n_sections)
- 인용 마커 몇 건이 걸려 있나 (n_citations)
- **이 자료가 유일한 근거인 절**이 있나 (sole) — 가장 아픈 경우다. 그 절은 제외 후
  근거가 0이 되어, 다시 쓰지 않으면 무근거 서술만 남는다.

sections.source_ids는 이름과 달리 **청크 id**다. 자료 단위로 보려면 청크→자료로 한 번
접어야 한다(drift 판정과 같은 접기).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.chunk import Chunk
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ImpactedSection:
    section_id: UUID
    label: str  # "2.3 인구·고령화 영향"
    n_citations: int
    # 이 자료를 빼면 이 절의 근거가 0이 된다 — 가장 아픈 경우.
    sole: bool
    locked: bool


@dataclass(frozen=True)
class SourceImpact:
    n_sections: int
    n_citations: int
    n_sole: int
    sections: tuple[ImpactedSection, ...]


async def measure_source_impact(
    session: AsyncSession, project_id: UUID, source_id: UUID
) -> SourceImpact:
    """이 자료를 제외했을 때 영향을 받는 절 — 목차 순서로.

    이미 제외된 자료도 그대로 센다(다시 채택했을 때의 이득을 같은 화면에서 읽는다).
    """
    # 청크 → 자료. 이 프로젝트 전체를 한 번에 들고 와 절마다 되짚는다.
    chunk_to_source = {
        cid: sid
        for cid, sid in (
            await session.execute(
                select(Chunk.id, Chunk.source_id).where(Chunk.project_id == project_id)
            )
        ).all()
    }
    # 지금 채택된 자료만이 "남는 근거"의 후보다 — 이미 빠진 자료는 위로가 못 된다.
    included = set(
        (
            await session.execute(
                select(ProjectSource.id).where(
                    ProjectSource.project_id == project_id,
                    ProjectSource.is_included.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    rows = (
        await session.execute(
            select(
                Section.id,
                Section.chapter_number,
                Section.section_number,
                Section.title,
                Section.source_ids,
                Section.locked,
                Section.content,
            ).where(Section.project_id == project_id)
        )
    ).all()

    hits: list[ImpactedSection] = []
    for row in sorted(rows, key=lambda r: (r.chapter_number, r.section_number)):
        chunk_ids = list(row.source_ids or [])
        mine = [cid for cid in chunk_ids if chunk_to_source.get(cid) == source_id]
        if not mine:
            continue
        # 이 자료를 뺐을 때 남는 근거 자료(채택 상태이고 이 자료가 아닌 것).
        remaining = {
            s
            for s in (chunk_to_source.get(cid) for cid in chunk_ids)
            if s is not None and s != source_id and s in included
        }
        hits.append(
            ImpactedSection(
                section_id=row.id,
                label=f"{row.chapter_number}.{row.section_number} {row.title}",
                n_citations=len(mine),
                sole=not remaining and bool((row.content or "").strip()),
                locked=bool(row.locked),
            )
        )

    impact = SourceImpact(
        n_sections=len(hits),
        n_citations=sum(h.n_citations for h in hits),
        n_sole=sum(1 for h in hits if h.sole),
        sections=tuple(hits),
    )
    logger.info(
        "source_impact.measured",
        project_id=str(project_id),
        source_id=str(source_id),
        n_sections=impact.n_sections,
        n_sole=impact.n_sole,
    )
    return impact
