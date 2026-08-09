from __future__ import annotations

from pydantic import BaseModel, Field

# 프론트 계약(web/src/api/types.ts) 1:1
# - 트리: ChapterNode(level=1) → SectionNode(level 1~4)
# - 본문: SectionContentResponse

SectionStatus = str  # pending | writing | completed | failed
QaStatus = str  # passed | failed | pending


class SectionNode(BaseModel):
    id: str
    title: str
    level: int
    status: str
    parent_id: str


class ChapterNode(BaseModel):
    id: str
    title: str
    level: int = 1
    status: str
    children: list[SectionNode] = Field(default_factory=list)


class SectionTreeResponse(BaseModel):
    tree: list[ChapterNode]


class SectionCitation(BaseModel):
    """본문 [N] 마커 ↔ 원본 자료 매핑 한 줄.

    number는 본문에 실제 등장하는 인용 번호. 편집으로 본문 마커 수가
    저장된 출처 수와 어긋나면 남는 출처는 number=None으로 내려간다.
    """

    number: int | None = None
    title: str
    url: str | None = None
    source_id: str | None = None
    reliability: str | None = None  # high | medium | low (수집기 기록값)


class UngroundedNumbers(BaseModel):
    """인용 근거에서 확인되지 않는 수치 — 조회 시점 재계산(편집 결과가 즉시 반영)."""

    count: int = 0
    samples: list[str] = Field(default_factory=list, description="앞부분 표본(최대 12개)")


class SectionContentResponse(BaseModel):
    id: str
    title: str
    content: str
    source_ids: list[str]
    qa_status: str
    level: int
    citations: list[SectionCitation] = Field(default_factory=list)
    # 인용 근거에 없는 수치 — 창작 위험 신호. 예타 실증(2026-08-09)에서 자료 없는 절이
    # 예산·계수를 지어내고 인용만 붙이는 사례가 나와, 절 화면에 직접 노출한다.
    ungrounded: UngroundedNumbers = Field(default_factory=UngroundedNumbers)


class SectionRewriteRequest(BaseModel):
    instruction: str = Field(
        "", max_length=2000, description="AI 재작성 지시(빈 문자열이면 근거 기반 단순 재작성)"
    )


class SectionBlockRewriteRequest(BaseModel):
    block: str = Field(..., min_length=1, description="본문에서 재작성할 블록의 원문(정확 일치)")
    instruction: str = Field(
        "", max_length=2000, description="블록 재작성 지시(빈 문자열이면 문장 다듬기)"
    )


class SectionContentUpdate(BaseModel):
    content: str = Field(..., description="수정한 섹션 본문(마크다운/개조식)")
