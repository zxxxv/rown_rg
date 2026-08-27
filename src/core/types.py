from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.clock import now


class Role(StrEnum):
    """
    사용자 역할 (권한 계층 단일 진실).

    DB users.role 제약과 API UserRole이 모두 이 enum에서 파생된다.
    계층 순서·admin 그룹 등 "인가 정책"은 api/dependencies/permissions.py가 담는다.
    """

    SUPER_ADMIN = "super_admin"  # 전권 (유저 삭제 등 최상위 작업)
    ADMIN = "admin"  # 유저 관리 (자기 이하 역할 부여)
    WORKER = "worker"  # 보고서 작성 작업자
    VIEWER = "viewer"  # 열람 전용 (SSO JIT 기본값)


class ProjectStage(StrEnum):
    """
    프로젝트 진행 단계 (라이프사이클 단일 진실).

    DB projects.status 제약과 API ProjectStatus가 모두 이 enum에서 파생된다.
    "어느 검토 게이트인지"는 status가 아니라 UserReviewPoint.gate(ReviewGate)가 담는다.
    """

    CREATED = "created"  # 생성 직후, 작업 시작 전
    PLANNING = "planning"  # 설계 브리프 작성·검토 (수집 전)
    RESEARCHING = "researching"  # 자료 검색·수집
    INDEXING = "indexing"  # 청킹·임베딩, 벡터 인덱스 구축
    WRITING = "writing"  # 섹션별 초안 작성
    REVIEWING = "reviewing"  # QA·사용자 검토 게이트 대기
    COMPLETED = "completed"  # 작성·검토 완료, 최종 산출물 확정
    ARCHIVED = "archived"  # 완료본 보관 처리
    CANCELLED = "cancelled"  # 사용자가 실행 도중 취소


class SourceType(StrEnum):
    """
    자료 출처 종류
    """

    LIBRARY = "library"
    UPLOAD = "upload"
    WEB_SEARCH = "web_search"


class ReviewGate(StrEnum):
    """
    사용자 검토 게이트
    """

    DESIGN_BRIEF = "design_brief"  # 설계 브리프 확정 (수집 전 — 검색 질의·중복 확인)
    SOURCE_POOL = "source_pool"  # 자료 풀 확정
    CONTRADICTION = "contradiction"  # 모순 해결
    LEVEL_1 = "level_1"  # 전체 요약
    LEVEL_2 = "level_2"  # 챕터 요약
    QA_SELECT = "qa_select"  # 섹션별 후보 선택 (정적 게이트 통과분 중 사람이 픽)
    FINAL = "final"  # 최종 편집


# 자료
class SourceRef(BaseModel):
    """
    자료 풀의 한 항목

    signal 필드(reliability~has_content)는 SOURCE_POOL 게이트에서 사람이 자료를
    취사선택할 때 보여줄 판단 근거다. 웹 수집 시 채워지며, 업로드/라이브러리 등
    신호가 없는 출처는 기본값으로 남는다(하위호환).
    """

    id: UUID
    source_type: SourceType
    title: str
    url: str | None = None
    library_node_id: UUID | None = None
    upload_path: str | None = None
    # 자료 확정 게이트용 신호
    reliability: str | None = None  # high | medium | low
    matched_sections: list[str] = Field(default_factory=list)  # 이 출처가 뒷받침하는 목차 섹션
    page_age: str | None = None  # 콘텐츠 최신성(원문 게시 시점)
    # 서지 조각(출처 표기용, 2026-08-27) - 미상이면 None으로 두고 표기에서 생략한다.
    publisher: str | None = None  # 발행기관("국제무역통상연구원")
    issue_label: str | None = None  # 호수("2024년 17호")
    published_year: int | None = None  # 발행연도(호수 없을 때의 폴백 표기)
    preview: str | None = None  # 본문 앞부분 미리보기
    has_content: bool = True  # 본문 회수·색인 성공 여부(False면 검색에 안 잡힘)


class SourceCandidate(BaseModel):
    """
    사용자 검토 전 검색 결과 후보
    """

    title: str
    url: str | None = None
    snippet: str | None = None
    source_type: SourceType


# 검색
class RetrievedChunk(BaseModel):
    """
    검색된 청크 1개
    """

    chunk_id: UUID
    source_id: UUID
    content: str
    score: float
    # 이 근거가 '무엇의 어디'인지 - 프롬프트에 함께 실어 모델이 귀속을 정확히 하게 한다.
    # 본문만 주면 모델은 근거 8이 정부 발표문인지 언론 요약인지 모른 채 인용한다
    # (2026-08-11 실측: Sonnet 인용 20건 중 10건이 문장을 뒷받침하지 못했고, 그중
    # 여럿이 없는 날짜·기관을 붙인 귀속 오류였다).
    source_title: str = ""
    header_path: list[str] = Field(default_factory=list)
    # 자료 발간연도(색인 시 추출, chunk.metadata.published_year) — 프롬프트 라벨에
    # 실어 옛 자료('21~'22)의 통계가 무연도 현재형으로 서술되는 것을 막는 재료.
    # 검증런 2회 연속의 주 감점 축이었다(2026-08-15). None=미상.
    published_year: int | None = None
    # RAPTOR 요약 노드 여부 — True면 프롬프트에서 '배경 맥락'으로만 쓰이고
    # [번호] 인용 풀에서 제외된다(인용 무결성은 leaf 청크 계약 유지).
    is_summary: bool = False


class RetrievalResult(BaseModel):
    """
    retrieve_for_section 함수의 반환값
    """

    chunks: list[RetrievedChunk]


# 작성
class SectionPlan(BaseModel):
    """
    작성할 섹션 1개의 계획

    direction·key_points·analysts는 프리셋 기반 설계(planner) 시 채워진다.
    analysts는 src.prompts 분석 에이전트 이름(AnalystSpec.name) 참조.
    """

    section_id: UUID = Field(default_factory=uuid4)
    chapter_number: int
    section_number: int
    title: str
    # 이 절이 속한 장의 제목. 검색·작성이 "2.3이 EU CBAM 장 소속"임을 알아야 한다 —
    # 없던 시절엔 장 제목이 config.outline에만 있어 검색 질의와 작성 프롬프트 어디에도
    # 안 실렸고, 4개 장에 같은 절 제목이 반복되면 네 절이 같은 자료를 인용했다
    # (2026-08-14 탄소규제 런 실측: 1.3과 2.3의 인용 자료 집합이 완전 동일).
    chapter_title: str = ""
    direction: str = ""
    key_points: list[str] = Field(default_factory=list)
    analysts: list[str] = Field(default_factory=list)
    # 이 절을 자료 풀에서 찾을 검색 질의 — 설계 브리프 단계에서 LLM이 절마다 만들어
    # 넣는다(brief_ai). 검색 질의를 사람이 쓰게 하면 대부분 비워 두므로(개인 에이전트
    # spec.queries가 전부 빈 배열이었던 이유) 기본값을 기계가 만든다. 사람은 브리프
    # 게이트 화면에서 보고 고친다. 비어 있으면 절 제목·핵심 포인트만으로 검색한다.
    search_queries: list[str] = Field(default_factory=list)
    # 이 절이 앞 절의 확정값 위에서 쓰는 의존 계약 — 표기는 core/builds_on.parse_ref
    # ("4.1" | "4.1(총사업비)" | "4.*"). 사람 소유(폼·프리셋), 자유주제만 플래너가
    # 절 번호 한정으로 생성. 값 전달은 서술 요약이 아니라 사실 대장 엔트리로 한다
    # (요약 체인은 무근거 +39% 실측 - services/ledger).
    builds_on: list[str] = Field(default_factory=list)
    # 이 절만의 분량 목표(자) — 없으면 에이전트 spec의 volume_target을 쓴다.
    # 절마다 요구 분량이 다른 자리가 있다: 시사점·제언은 본론과 같은 두께로 쓰면
    # 앞 내용을 되풀이하게 되므로 짧게 눌러야 한다(2026-08-24 사용자 지시:
    # "시사점은 중요 내용 위주로 3~5페이지 이내"). 에이전트는 여러 절이 공유하니
    # 그쪽 값을 고치면 남의 절까지 바뀐다 — 절 단위 상한이 필요한 이유다.
    min_chars: int | None = None
    max_chars: int | None = None

    def prompt_label(self) -> str:
        """프롬프트에 싣는 절 표기 — '2.3 국내 기업 대응수준 진단 (2장 EU CBAM)'.

        장 맥락이 빠지면 비교형 목차에서 모델은 이 절이 어느 규제를 다루는지 알 방법이
        없다. 2026-08-14 실측에서 2.3·3.3·4.3이 전부 '국내 기업 대응수준 진단 및
        조사항목 도출'이라는 같은 제목만 받아 서로 구별되지 않는 글이 나왔다.
        """
        label = f"{self.chapter_number}.{self.section_number} {self.title}"
        if self.chapter_title and self.chapter_title.lower() not in self.title.lower():
            label += f" ({self.chapter_number}장 {self.chapter_title})"
        return label


class SectionDraft(BaseModel):
    """
    Writer가 생성한 섹션 초안
    """

    section_id: UUID
    content: str
    cited_chunk_ids: list[UUID]
    # 프롬프트에 실린 인용 가능 청크(순서 = 절-로컬 [n] 번호). 인용한 것만 남기면
    # "보고도 안 쓴 근거"와 "안 보고 쓴 주장"을 가를 수 없다. 재작성 경로도 이 값을
    # 절 meta로 넘겨 근거 추적이 반쪽이 되지 않게 한다.
    pool_chunk_ids: list[UUID] = Field(default_factory=list)
    # 분할 계획이 무너져 단일 호출로 떨어졌는가. 그러면 절이 짧아지고 인용이 줄어드는데
    # 지금까지 아무 신호가 없어 짧고 근거 얇은 절이 조용히 보고서에 실렸다(2026-08-11).
    split_fallback: bool = False
    # 재료 부족으로 분량 목표를 내려 썼는가 — 재작성 경로가 절 meta(volume_scaled)를
    # 이 값으로 갱신한다. 안 갱신하면 자료를 채워 다시 써도 '자료 부족' 배지가 남는다.
    volume_scaled: bool = False
    # 생성이 완결되지 못한 이유(provider stop_reason). 빈 문자열이면 정상 완결.
    # max_tokens 컷·refusal이 문장 중간에 멈춘 216자 토막을 '완성 절'로 통과시킨
    # 실사고(2026-08-13, 7.1절) 재발 방지 — 게이트가 이 값으로 HARD 제외한다.
    incomplete_reason: str = ""


# QA 후보 검사 — AI는 후보만 생성, 합격/불합격은 정적 코드, 최종 선택은 사람.
class CheckSeverity(StrEnum):
    """
    정적 검사 실패의 처리 강도.
    """

    HARD = "hard"  # 실패 → 후보 제외 (사람에게 노출 안 함)
    SOFT = "soft"  # 실패 → 경고만, 사람에게 주석으로 표시


class GateResult(BaseModel):
    """
    정적 검사 1건의 결과
    """

    check: str  # 검사 이름 (예: "citation_resolves")
    severity: CheckSeverity
    passed: bool
    detail: str | None = None  # 실패 사유 — 사람이 읽는 설명


class StaticCheckReport(BaseModel):
    """
    한 후보에 대한 정적 검사 종합
    """

    results: list[GateResult] = Field(default_factory=list)

    @property
    def excluded(self) -> bool:
        """HARD 검사가 하나라도 실패하면 후보 제외."""
        return any(not r.passed and r.severity is CheckSeverity.HARD for r in self.results)

    @property
    def warnings(self) -> list[GateResult]:
        """실패한 SOFT 검사 — 사람에게 표시할 경고."""
        return [r for r in self.results if not r.passed and r.severity is CheckSeverity.SOFT]


class SectionCandidate(BaseModel):
    """
    섹션 1개에 대한 후보 초안 + 정적 검사 결과
    """

    candidate_id: UUID = Field(default_factory=uuid4)
    draft: SectionDraft
    report: StaticCheckReport = Field(default_factory=StaticCheckReport)


class SectionCandidateSet(BaseModel):
    """
    한 섹션의 후보 N개 묶음
    """

    section_id: UUID
    candidates: list[SectionCandidate] = Field(default_factory=list)

    @property
    def survivors(self) -> list[SectionCandidate]:
        """HARD 검사를 통과해 사람에게 노출되는 후보들."""
        return [c for c in self.candidates if not c.report.excluded]


# 검토 게이트
class UserReviewPoint(BaseModel):
    """
    사용자 결정 대기 지점
    """

    id: UUID = Field(default_factory=uuid4)
    gate: ReviewGate
    created_at: datetime = Field(default_factory=now)
    payload: dict
    decision: dict | None = None
