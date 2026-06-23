from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.clock import now


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
    FINAL = "final"  # 최종 편집


# 자료
class SourceRef(BaseModel):
    """
    자료 풀의 한 항목
    """

    id: UUID
    source_type: SourceType
    title: str
    url: str | None = None
    library_node_id: UUID | None = None
    upload_path: str | None = None


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


class RetrievalResult(BaseModel):
    """
    retrieve_for_section 함수의 반환값
    """

    chunks: list[RetrievedChunk]


# 작성
class SectionPlan(BaseModel):
    """
    작성할 섹션 1개의 계획
    """

    section_id: UUID = Field(default_factory=uuid4)
    chapter_number: int
    section_number: int
    title: str


class SectionDraft(BaseModel):
    """
    Writer가 생성한 섹션 초안
    """

    section_id: UUID
    content: str
    cited_chunk_ids: list[UUID]


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
