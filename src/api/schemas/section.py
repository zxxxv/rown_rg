from __future__ import annotations

from pydantic import BaseModel, Field

# 프론트 계약(web/src/api/types.ts) 1:1
# - 트리: ChapterNode(level=1) → SectionNode(level 1~4)
# - 본문: SectionContentResponse

SectionStatus = str  # pending | writing | completed | failed
QaStatus = str  # passed | failed | pending


# 절 본문 상한 — 실측 최대 절이 18,933자, 평균 10,561자다(2026-08-27, 전 프로젝트).
# 10배를 열어 두면 정상 편집은 절대 걸리지 않고, 무제한이던 구멍만 막힌다.
#
# 왜 막는가: 파일 업로드는 50MB로 막아 뒀는데 JSON 본문은 아무 제한이 없었다. 거대한
# 본문이 한 번 저장되면 그 뒤 **모든 버전 스냅샷이 그것을 통째로 복사**하고(스냅샷은
# 보고서 전체를 담는다), HWPX 렌더와 시사점 빌더가 함께 막힌다.
MAX_SECTION_CHARS = 200_000


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


class FigurePlaceholder(BaseModel):
    """이 절에 들어갈 그림 자리표시자 — 한글 산출물에만 있던 것을 화면에도 알린다.

    자리표시자는 조립 단계에서 만들어져 본문(content)에는 없다. 그래서 검토자는
    한글 파일을 열기 전까지 어디에 그림이 들어갈지 몰랐다(2026-08-24 지적).
    """

    caption: str
    description: str
    # 원본 그림을 찾아갈 자료 — "제목 (URL)" 꼴. 눌러 보고 다시 그릴지 따다 쓸지 판단.
    source_hints: list[str] = Field(default_factory=list)


class LostEvidenceBlock(BaseModel):
    """자료 제외로 근거 표기를 잃은 문단 1개.

    절 전체 재작성은 실측 $0.4~$1.3인데 이 문단만 고치면 블록 재작성 1콜이다 - 십수 배
    싸다. 어디를 고치면 되는지 짚어 주려면 마커가 지워지는 그 순간 자리를 남겨야 한다
    (지워진 마커는 흔적이 없다).
    """

    text: str
    n_markers: int = 0


class SectionContentResponse(BaseModel):
    id: str
    title: str
    content: str
    source_ids: list[str]
    qa_status: str
    level: int
    citations: list[SectionCitation] = Field(default_factory=list)
    evidence: EvidenceInfo = Field(default_factory=EvidenceInfo)
    figures: list[FigurePlaceholder] = Field(default_factory=list)
    # 잠근 절 - AI 재작성 경로가 막힌다(0048). 사람의 직접 편집은 그대로.
    locked: bool = False
    # 자료를 빼면서 근거 표기를 잃은 문단들 - 화면이 그 블록만 짚어 다시 쓰게 한다.
    evidence_lost: list[LostEvidenceBlock] = Field(default_factory=list)


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
    page: int | None = None  # PDF 원본에서의 시작 페이지(1-기반) - PDF 외 자료는 None


class GroundedNumberRead(BaseModel):
    """본문 수치 하나 ↔ 근거 원문에서 발견된 자리(청크 문자 오프셋).

    ungrounded의 반대 방향 - 위치를 가리킬 뿐 뒷받침 판정이 아니다. 화면은 이
    오프셋으로 근거 카드·원문 뷰어의 해당 줄로 점프한다.
    """

    token: str
    chunk_id: str
    start: int
    end: int
    text: str = ""  # 발견된 줄(표 행이면 길다) - 점프의 본체는 오프셋이라 잘라 보낸다


class SpanCandidateRead(BaseModel):
    """확정 못 한 문장에 내놓는 후보 대목 하나."""

    chunk_id: str
    start: int
    end: int
    text: str
    # 어휘 겹침(한글 대 한글) 또는 다국어 임베딩 코사인(교차언어). 둘 중 하나만 유효하다 -
    # 어느 자로 순위를 매겼는지 화면이 알아야 "추정 근거"를 정직하게 말할 수 있다.
    score: float = 0.0
    dense_score: float | None = None


class NumberRelocationRead(BaseModel):
    """무근거 수치의 실제 소재 제안 - 판정이 아니라 사람이 확인할 후보 하나."""

    token: str
    number: int  # 그 수치가 실재하는 근거의 인용 번호
    chunk_id: str


class InjectionSuspectRead(BaseModel):
    """연도를 명시한 수치의 주입 의심 - 제목이 있으면 시점 불일치, 없으면 소재 불명."""

    token: str
    located_title: str | None = None


class ClaimAlignmentRead(BaseModel):
    """본문 문장 하나 ↔ 그 문장이 나온 근거 대목.

    청크(수백~수천 자)까지만 알려주면 사람이 다시 읽어야 한다. 어휘 겹침으로 청크
    안의 줄까지 좁히고, 못 좁히면 그 사실을 status로 돌려준다(LLM 판정 아님).
    """

    claim: str
    numbers: list[int] = Field(default_factory=list)
    # aligned(대목 특정) | weak(겹침 약함, 추정) | unmatched(근거에서 못 찾음)
    # | uncited(표기 없음) | crosslingual(근거가 외국어라 겹침 판정 불가)
    status: str
    chunk_id: str | None = None
    span_start: int | None = None  # 청크 본문 안에서의 문자 위치 - 화면이 그 대목만 강조한다
    span_end: int | None = None
    span_text: str | None = None
    score: float = 0.0
    ungrounded: list[str] = Field(default_factory=list)  # 이 문장에서 근거에 없는 수치
    # 무근거 수치 중 절의 **다른** 근거에는 있는 것 - 오귀속 교정 제안. 화면이
    # "이 수치는 출처 n에 있습니다"를 보여주면 표기 고치기가 클릭 하나가 된다.
    relocations: list[NumberRelocationRead] = Field(default_factory=list)
    # 연도 명시 수치의 주입 의심(3단 판정의 결정적 부분만) - "지어냈거나 옛 지식"을
    # 문장 옆에서 말하고, 조치는 국소 재작성(rewrite-block)으로 잇는다.
    injections: list[InjectionSuspectRead] = Field(default_factory=list)
    grounded: list[GroundedNumberRead] = Field(default_factory=list)  # 근거에서 찾은 수치 위치
    # 확정하지 못했을 때 사람이 고를 후보 대목(순위 내림차순). 대목을 단정하면 거짓
    # 확신이 되지만 후보로 내놓으면 기계가 좁히고 사람이 고르는 것이 된다.
    # aligned인 문장에는 실리지 않는다 - 이미 답이 있는데 후보를 늘어놓을 이유가 없다.
    candidates: list[SpanCandidateRead] = Field(default_factory=list)


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
    # 주장 단위로 안 잡혀 어떤 검사도 못 본 줄 - 명사 종결 + 수치 없는 개조식 줄이
    # 대부분이다. 화면이 "대조 안 함"으로 짚어 준다(안 그러면 밑줄 없는 이유를 알 수 없다).
    uncovered: list[str] = Field(default_factory=list)
    # 줄 회계 - 화면이 분모를 보여줄 수 있게(services/qa/gate.line_accounting).
    # "86개 중 2개 확인"은 86이 무엇인지 모르면 좋은 비율인지 알 수 없다.
    body_lines: int = 0  # 빈 줄·코드펜스를 뺀 본문 줄
    counted_lines: int = 0  # 그중 근거 대조 대상이 된 줄(나머지는 제목·표·캡션·짧은 줄)
    # 마커를 청크까지 되짚을 수 있는가. 옛 절(기록 없음)은 자료 단위까지만 가능하다.
    traceable: bool = True


class SourceChunkRead(BaseModel):
    """원문 뷰어의 문서 조각 - 색인 청크를 원문 순서 그대로 내려보낸다."""

    chunk_id: str
    content: str
    chunk_index: int | None = None
    header_path: list[str] = Field(default_factory=list)
    page: int | None = None  # PDF 원본에서의 시작 페이지(1-기반) - PDF 외 자료는 None


class SourceDocumentResponse(BaseModel):
    """자료 하나의 색인 본문 전체 - 근거 패널의 '원문에서 위치 보기'용.

    파일을 다시 파싱하지 않는다(PDF 재파싱은 수십 초). 색인 청크가 이미 파싱 원문의
    순서 보존 조각이고, 근거 추적의 span 오프셋이 청크 기준이라 청크 경계를 살려
    보내야 화면이 대목을 특정할 수 있다. 웹·업로드·라이브러리 자료 공통 경로.
    """

    source_id: str
    title: str | None = None
    url: str | None = None
    source_type: str = ""  # library | upload | web_search
    chunks: list[SourceChunkRead] = Field(default_factory=list)


class SectionRewriteRequest(BaseModel):
    instruction: str = Field(
        "", max_length=2000, description="AI 재작성 지시(빈 문자열이면 근거 기반 단순 재작성)"
    )


class SectionBlockRewriteRequest(BaseModel):
    block: str = Field(
        ...,
        min_length=1,
        max_length=MAX_SECTION_CHARS,
        description="본문에서 재작성할 블록의 원문(정확 일치)",
    )
    instruction: str = Field(
        "", max_length=2000, description="블록 재작성 지시(빈 문자열이면 문장 다듬기)"
    )


class SectionContentUpdate(BaseModel):
    content: str = Field(
        ...,
        max_length=MAX_SECTION_CHARS,
        description="수정한 섹션 본문(마크다운/개조식)",
    )
