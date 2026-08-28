"""완성 보고서의 자료 사용 통계 — 전체/장/절 3레벨 집계.

근거 추적(블록→청크 원문)이 미시라면 이건 거시다: 자료별 참조 횟수를 세어
"이 보고서가 어떤 자료 위에 서 있는가"와 편중(한 자료 집중)·미사용 채택 자료를
드러낸다. 번호 해석은 조립 후 규약(전역 번호 n = 채택 자료의 수집 순 n번 —
api.routers.projects._citations_from_numbers와 같은 단일 진실)을 따르므로,
호출자는 조립을 지난(completed/archived) 프로젝트에만 써야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.api.schemas.source_stats import (
    ChapterUsage,
    SectionUsage,
    SourceUsageItem,
    SourceUsageResponse,
)
from src.core.citations import count_numbers
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section


def build_source_usage(
    rows: Sequence[Section],
    sources_ordered: Sequence[ProjectSource],
) -> SourceUsageResponse:
    """섹션 본문 마커를 세어 자료별·장별·절별 사용 통계로 묶는다.

    범위 밖 번호(> 채택 자료 수)는 버린다 — 조립(renumber)이 지웠어야 정상이고,
    남았다면 수동 편집 잔재라 통계를 오염시키지 않는 쪽이 낫다.
    """
    n_sources = len(sources_ordered)
    section_stats: list[SectionUsage] = []
    chapter_acc: dict[int, ChapterUsage] = {}
    total_counts: dict[int, int] = {}
    sections_used: dict[int, set[int]] = {}  # 전역 번호 → 참조한 절 인덱스

    for idx, row in enumerate(sorted(rows, key=lambda r: (r.chapter_number, r.section_number))):
        counts = {n: c for n, c in count_numbers(row.content or "").items() if 1 <= n <= n_sources}
        section_stats.append(
            SectionUsage(
                section_id=row.id,
                chapter_number=row.chapter_number,
                section_number=row.section_number,
                title=row.title,
                citations=sum(counts.values()),
                n_sources=len(counts),
                counts=counts,
            )
        )
        ch = chapter_acc.get(row.chapter_number)
        if ch is None:
            ch = ChapterUsage(
                chapter_number=row.chapter_number,
                title=row.chapter_title,
                citations=0,
                counts={},
            )
            chapter_acc[row.chapter_number] = ch
        for n, c in counts.items():
            ch.counts[n] = ch.counts.get(n, 0) + c
            total_counts[n] = total_counts.get(n, 0) + c
            sections_used.setdefault(n, set()).add(idx)
        ch.citations += sum(counts.values())

    def _item(number: int, src: ProjectSource) -> SourceUsageItem:
        return SourceUsageItem(
            number=number,
            source_id=src.id,
            title=src.title or src.url or "(제목 없음)",
            url=src.url,
            origin=src.source_type,
            reliability=src.reliability,
            citations=total_counts.get(number, 0),
            sections_used=len(sections_used.get(number, ())),
            published_year=year
            if isinstance((year := (src.metadata_ or {}).get("published_year")), int)
            else None,
        )

    items = [_item(i + 1, src) for i, src in enumerate(sources_ordered)]
    used = sorted((x for x in items if x.citations > 0), key=lambda x: (-x.citations, x.number))
    unused = [x for x in items if x.citations == 0]

    return SourceUsageResponse(
        total_citations=sum(total_counts.values()),
        sources=used,
        chapters=[chapter_acc[k] for k in sorted(chapter_acc)],
        sections=section_stats,
        unused=unused,
    )
