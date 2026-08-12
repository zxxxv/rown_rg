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
    # 작성 시 근거가 부족해 분량 목표를 내린 절 — 목록에서 바로 눈에 띄게 한다.
    evidence_scarce: bool = False


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


class EvidenceInfo(BaseModel):
    """이 절이 쓸 수 있었던 근거의 양 — 작성 시점에 기록(sections.meta).

    자료가 부족하면 분량 목표를 내려서 쓴다. 그 사실을 본문에 적으면 납품물이
    더러워지므로 본문 대신 이 플래그로 화면에 알린다.
    """

    count: int | None = None  # 검색된 인용 가능 청크 수(None=옛 절, 기록 없음)
    scarce: bool = False  # 재료 부족으로 분량 목표를 내렸는지
    # 분할 계획이 무너져 단일 호출로 쓴 절 - 짧고 인용이 얇아진다. 재작성을 권할 신호.
    plan_failed: bool = False


class SectionContentResponse(BaseModel):
    id: str
    title: str
    content: str
    source_ids: list[str]
    qa_status: str
    level: int
    citations: list[SectionCitation] = Field(default_factory=list)
    evidence: EvidenceInfo = Field(default_factory=EvidenceInfo)


class EvidenceChunk(BaseModel):
    """본문 [n]이 실제로 가리킨 근거 조각 - 모델이 프롬프트로 받은 그 원문이다.

    출처(자료) 단위 표기로는 "이 자료 어딘가"까지만 알 수 있다. 여기서는 청크
    단위로 내려보내 원문 대목을 직접 대조할 수 있게 한다.
    """

    number: int | None = None  # 본문 인용 번호. None = 프롬프트에 실렸지만 인용되지 않은 근거
    chunk_id: str
    content: str
    cited: bool = True
    source_id: str | None = None
    source_title: str | None = None
    url: str | None = None
    reliability: str | None = None
    header_path: list[str] = Field(default_factory=list, description="원본 문서 안의 소제목 경로")
    chunk_index: int | None = None  # 원본 문서 안에서의 순번


class ClaimAlignmentRead(BaseModel):
    """본문 문장 하나 ↔ 그 문장이 나온 근거 대목.

    청크(수백~수천 자)까지만 알려주면 사람이 다시 읽어야 한다. 어휘 겹침으로 청크
    안의 줄까지 좁히고, 못 좁히면 그 사실을 status로 돌려준다(LLM 판정 아님).
    """

    claim: str
    numbers: list[int] = Field(default_factory=list)
    # aligned(대목 특정) | weak(겹침 약함, 추정) | unmatched(근거에서 못 찾음) | uncited(표기 없음)
    status: str
    chunk_id: str | None = None
    span_start: int | None = None  # 청크 본문 안에서의 문자 위치 - 화면이 그 대목만 강조한다
    span_end: int | None = None
    span_text: str | None = None
    score: float = 0.0
    ungrounded: list[str] = Field(default_factory=list)  # 이 문장에서 근거에 없는 수치


class SectionEvidenceResponse(BaseModel):
    """절 하나의 근거 추적 결과 - 인용된 근거 + 실렸는데 안 쓰인 근거 + 무근거 신호."""

    section_id: str
    items: list[EvidenceChunk] = Field(default_factory=list)
    claims: list[ClaimAlignmentRead] = Field(default_factory=list)
    # 문장별 상태 집계 - 화면 배지가 이것만 읽는다(목록을 다시 세지 않게)
    aligned_count: int = 0
    weak_count: int = 0
    unmatched_count: int = 0
    pool_size: int = 0  # 작성 때 프롬프트에 실린 인용 가능 근거 수(옛 절은 0)
    cited_count: int = 0
    unused_count: int = 0
    uncited_count: int = 0  # 근거 표기가 없는 주장 수
    uncited_samples: list[str] = Field(default_factory=list)
    # 마커를 청크까지 되짚을 수 있는가. 옛 절(기록 없음)은 자료 단위까지만 가능하다.
    traceable: bool = True


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
