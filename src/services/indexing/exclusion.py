"""0청크 자료 자동 제외 — 인용 불가능한 자료가 근거 풀·전역 번호 자리를 차지하지 않게.

색인이 끝났는데 청크가 하나도 없는 자료는 검색될 수 없고 인용될 수도 없다. 그런데
is_included=true로 남으면 전역 인용 번호(채택 자료 수집 순서)의 한 자리를 차지해
출처 최종장에 유령 항목이 실린다(2026-08-12 사용자 보고: 0청크 실패 업로드).

사람의 제외 결정과 구분하려고 metadata에 auto_excluded를 남긴다 — 재색인·재수집이
본문을 채우면 자동 제외만 되돌리고, 사람이 제외한 자료는 그대로 둔다. 이미 완료된
프로젝트의 채택 상태는 건드리지 않으므로(색인 시점에만 동작) 확정된 번호 체계는
소급해서 흔들리지 않는다.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import func, select

from src.db.models.chunk import Chunk
from src.db.models.project_source import ProjectSource

logger = structlog.get_logger(__name__)

AUTO_EXCLUDED_KEY = "auto_excluded"

_NO_CONTENT_MESSAGE = "색인된 본문이 없어 근거에서 제외했습니다 - 파일을 다시 올리면 복구됩니다."


def apply_index_outcome(row: ProjectSource, n_chunks: int) -> None:
    """색인 결과(청크 수)를 채택 상태에 반영한다. 커밋은 호출자 몫.

    0청크 → 자동 제외. 청크 생김 → 자동 제외였던 것만 채택 복구(사람 제외는 유지).
    """
    meta = dict(row.metadata_ or {})
    if n_chunks > 0:
        if meta.pop(AUTO_EXCLUDED_KEY, None):
            row.is_included = True
            row.metadata_ = meta
        return
    meta[AUTO_EXCLUDED_KEY] = True
    if not meta.get("index_error"):
        meta["index_error"] = _NO_CONTENT_MESSAGE
    row.is_included = False
    row.metadata_ = meta


async def auto_exclude_chunkless(project_id: UUID, source_ids: list[UUID]) -> int:
    """색인을 마쳤는데 청크가 없는 자료들을 일괄 자동 제외한다(색인 단계용).

    호출자가 '색인 안 됐다'고 넘겨도 DB 청크 수를 다시 세서 실제 0인 것만 제외한다 —
    재개(resume)로 색인 단계가 다시 돌 때 이전 런이 색인해 둔 자료를 오인하지 않게.
    """
    if not source_ids:
        return 0
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(ProjectSource).where(
                        ProjectSource.project_id == project_id,
                        ProjectSource.id.in_(source_ids),
                        ProjectSource.is_included.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        counts = dict(
            (
                await session.execute(
                    select(Chunk.source_id, func.count())
                    .where(Chunk.source_id.in_([r.id for r in rows]))
                    .group_by(Chunk.source_id)
                )
            ).all()
        )
        n = 0
        for row in rows:
            if counts.get(row.id, 0) == 0:
                apply_index_outcome(row, 0)
                n += 1
        await session.commit()
    if n:
        logger.info("indexing.auto_excluded", project_id=str(project_id), n=n)
    return n
