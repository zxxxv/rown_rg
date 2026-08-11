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
    direction: str = ""
    key_points: list[str] = Field(default_factory=list)
    analysts: list[str] = Field(default_factory=list)


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
