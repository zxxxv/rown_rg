"""자료 사용 통계 응답 — 완성 보고서가 '어떤 자료 위에 서 있는가'를 3레벨로.

번호 해석은 조립 후 규약(전역 번호 n = 채택 자료 n번, 수집 순)을 따르므로
완성(조립을 지난) 보고서에만 성립한다 — 라우터가 상태를 가드한다.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class SourceUsageItem(BaseModel):
    """자료 1건의 사용 요약. number는 참고문헌 전역 번호(본문 마커와 동일)."""

    number: int
    source_id: UUID
    title: str
    url: str | None = None
    origin: str  # upload | library | web_search (project_sources.source_type)
    reliability: str | None = None  # high | medium | low
    citations: int  # 본문 참조 수(참고 (출처 n) + 직접 인용 [n] 합산)
    sections_used: int  # 이 자료를 참조한 절 수


class ChapterUsage(BaseModel):
    chapter_number: int
    title: str
    citations: int
    counts: dict[int, int]  # 전역 번호 → 참조 수 (프론트 도넛·목록의 원천)


class SectionUsage(BaseModel):
    section_id: UUID
    chapter_number: int
    section_number: int
    title: str
    citations: int
    n_sources: int  # 이 절이 참조한 자료 수(근거 다양성 신호)
    counts: dict[int, int]


class SourceUsageResponse(BaseModel):
    total_citations: int
    sources: list[SourceUsageItem]  # 참조 수 내림차순(사용된 자료만)
    chapters: list[ChapterUsage]
    sections: list[SectionUsage]
    # 채택(is_included)됐지만 한 번도 참조되지 않은 자료 — "다음 런에서 빼거나
    # 바꿀 후보"라는 행동 유도가 이 통계의 존재 이유 중 하나다.
    unused: list[SourceUsageItem] = Field(default_factory=list)
