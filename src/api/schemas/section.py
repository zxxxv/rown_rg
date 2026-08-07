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


class SectionContentResponse(BaseModel):
    id: str
    title: str
    content: str
    source_ids: list[str]
    qa_status: str
    level: int
    citations: list[SectionCitation] = Field(default_factory=list)


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
