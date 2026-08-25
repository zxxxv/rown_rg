from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.types import ProjectStage

# status는 ProjectStage(core)를 단일 진실로 삼아 파생한다. 별도 Literal로 중복 정의하지 않는다.
ProjectStatus = ProjectStage
DepthMode = Literal["outline_only", "standard", "full_report", "deep_dive"]


class ProjectBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="프로젝트 제목")
    topic: str = Field(..., min_length=1, description="보고서 주제")


class ConfigUpdateRequest(BaseModel):
    """진행 중 옵션 변경 — config 전체 교체(프론트가 폼 전체 값을 보낸다)."""

    config: dict[str, Any]


class PresetRead(BaseModel):
    """생성 화면 프리셋 선택용 카탈로그 항목. 생성 시 preset에 id 또는 name을 넣는다.

    scope='personal'은 사용자가 저장한 목차 프리셋(id는 "u:<uuid>")이고, 시스템
    프리셋과 같은 선택지로 노출된다 - 구성 재사용(2026-08-12 QA 2번의 확장).
    """

    id: str
    name: str
    desc: str
    # 이 유형이 **무엇에 쓰는 보고서인지** 한 줄. 시스템 프리셋의 desc는 개수 문자열
    # ("7챕터 34섹션")이라 고를 근거가 못 된다(2026-08-25 전수 조사) — 목차 골격에
    # 딸린 domain_context의 첫 문장을 내려보내 카드에 싣는다. 없으면 None.
    summary: str | None = None
    n_chapters: int
    n_sections: int
    scope: Literal["system", "personal", "shared"] = "system"
    updated_at: datetime | None = None
    # shared일 때 누구 것인지 — 같은 이름이 둘일 때 가려 고르는 단서(에이전트와 같은 규약).
    owner_name: str | None = None
    # personal일 때 내 프리셋의 공개 여부 — 목록에서 바로 토글하려면 실려 와야 한다.
    is_public: bool = False


class PresetSectionRead(BaseModel):
    """프리셋 목차의 절 1개 — 생성 화면 목차 편집기의 초기값."""

    title: str
    direction: str = ""
    key_points: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    # 이 절이 앞 절의 확정값 위에서 쓰는 의존("4.1"|"4.1(지표)"|"4.*") - 사실 대장 주입
    builds_on: list[str] = Field(default_factory=list)


class PresetChapterRead(BaseModel):
    title: str
    sections: list[PresetSectionRead]


class PresetDetailRead(BaseModel):
    """프리셋 전체 골격 — 사용자가 클릭해 들어가 목차·에이전트를 편집하는 출발점."""

    id: str
    name: str
    desc: str
    domain_context: str = ""
    chapters: list[PresetChapterRead]


class UserPresetSectionIn(BaseModel):
    """개인 프리셋의 절 1개 — 프리셋 상세(agents 표기)와 같은 와이어 모양으로 받는다."""

    title: str = Field(..., min_length=1, max_length=255)
    direction: str = Field("", max_length=1000)
    key_points: list[str] = Field(default_factory=list, max_length=30)
    agents: list[str] = Field(default_factory=list, max_length=5)
    builds_on: list[str] = Field(default_factory=list, max_length=4)


class UserPresetChapterIn(BaseModel):
    title: str = Field("", max_length=255)
    sections: list[UserPresetSectionIn] = Field(default_factory=list, max_length=50)


class UserPresetUpsert(BaseModel):
    """개인 목차 프리셋 저장/수정 — 목차 편집기의 현재 구성을 이름 붙여 담는다."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=500)
    chapters: list[UserPresetChapterIn] = Field(..., min_length=1, max_length=50)
    # 켜면 전 계정의 프리셋 선택지에 뜬다(본인 토글).
    is_public: bool = False

    @model_validator(mode="after")
    def _check_sections(self) -> UserPresetUpsert:
        if not any(ch.sections for ch in self.chapters):
            raise ValueError("절이 1개 이상 있어야 프리셋으로 저장할 수 있습니다")
        return self


class PresetVisibilityUpdate(BaseModel):
    """공개 토글 전용 부분 수정 — 목록 화면이 outline 없이 켜고 끈다."""

    is_public: bool


class UserPresetRead(BaseModel):
    """개인 프리셋 1건 — key는 생성 폼 preset 값으로 그대로 쓰는 "u:<uuid>"."""

    id: UUID
    key: str
    name: str
    description: str | None
    n_chapters: int
    n_sections: int
    is_public: bool = False
    updated_at: datetime


class AnalystRead(BaseModel):
    """분석 에이전트 카탈로그 항목 — 섹션별 배정 UI용 (프롬프트 본문은 노출 안 함)."""

    id: str
    name: str
    cat: str
    desc: str
    pages: str | None = None  # volume_target 분량 안내 (예: "10~15")
    # 남이 만들어 공개한 에이전트인지 + 누구 것인지. 같은 이름이 둘일 때 사람이
    # 가려 고를 수 있는 유일한 단서라, 목록에 반드시 함께 내려간다.
    shared: bool = False
    owner_name: str | None = None
    # 검색 질의 템플릿("{topic}"=장·절 제목 치환) — 목차 편집기가 "이 절에서 무엇이
    # 검색되는가" 미리보기에 쓴다(2026-08-20). 프롬프트 본문은 여전히 안 내려간다.
    queries: list[str] = []


class OutlineSectionIn(BaseModel):
    """사용자 확정 목차의 절 1개 (config.outline). analysts는 카탈로그 이름 참조."""

    # 안정 식별자(uuid 문자열) - 절 정체성의 닻(core/outline.py). 없거나 깨졌으면
    # 서버가 발급하므로 선택 필드다. 클라이언트는 받은 id를 그대로 돌려보내야
    # 목차 편집이 절 정체성(계획·리허설·본문 행)을 보존한다.
    id: str | None = Field(None, max_length=36)
    title: str = Field(..., min_length=1, max_length=255)
    direction: str = ""
    key_points: list[str] = Field(default_factory=list)
    analysts: list[str] = Field(default_factory=list)
    # 의존 계약 - 저작은 번호("4.1")·저장은 id 토큰("s:<uuid>"). 검증·정규화는
    # 라우터 _validate_outline_config → core/outline.normalize_outline이 한다.
    builds_on: list[str] = Field(default_factory=list)


class OutlineChapterIn(BaseModel):
    id: str | None = Field(None, max_length=36)
    title: str = Field("", max_length=255)
    sections: list[OutlineSectionIn] = Field(default_factory=list)


class OutlineIn(BaseModel):
    """생성 화면에서 확정한 목차 — 있으면 planner LLM을 생략하고 그대로 실행된다."""

    chapters: list[OutlineChapterIn]


class ProjectCreate(ProjectBase):
    preset: str | None = Field(
        None,
        max_length=100,
        description="보고서 유형 프리셋 키 (예: 예비타당성조사). None=자유 주제",
    )
    config: dict[str, Any] = Field(default_factory=dict, description="모듈식 옵션")
    depth_mode: DepthMode = Field("full_report", description="보고서 깊이")


class ProjectUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    topic: str | None = Field(None, min_length=1)
    preset: str | None = None
    config: dict[str, Any] | None = None
    status: ProjectStatus | None = None
    depth_mode: DepthMode | None = None


class SourceItemRead(BaseModel):
    """자료 검토 페이지용 출처 1건 — project_sources 행 + metadata 신호."""

    id: UUID
    source_type: str  # library | upload | web_search
    title: str | None = None
    url: str | None = None
    reliability: str | None = None  # high | medium | low
    is_included: bool
    matched_sections: list[str] = Field(default_factory=list)
    page_age: str | None = None
    preview: str | None = None
    has_content: bool = False
    # 자료 발간연도(색인 추출 또는 page_age 파생) — '24년 이후' 같은 자료 기준을
    # 사람이 게이트에서 판단할 수 있게 목록에 배지로 노출한다. None=미상.
    published_year: int | None = None
    # 라이브러리에서 불러온 자료의 원본 노드 id — 트리에서 '이미 추가됨'을 표시하는 근거.
    library_node_id: UUID | None = None
    # 색인이 뒤에서 도는 중인지(업로드 직후). 화면은 '색인 중' 배지를 띄우고 새로고침한다.
    indexing: bool = False
    # 실행 전 업로드는 색인을 런의 색인 단계로 미룬다(2026-08-20) - 화면은 "실행 시
    # 색인" 배지로 '본문 없음'(실패 신호)과 구분한다.
    index_deferred: bool = False
    # 색인 실패 사유(사람이 읽는 한 줄). 실패를 조용히 삼키면 0조각 자료가 방치된다.
    index_error: str | None = None
    # 파일 자료의 실물 신호 — 목록에서 "올라갔나·본문이 들어갔나"를 눈으로 가른다.
    # 2026-08-20 사고: 동시 색인 메모리 부족으로 8건이 조각 0으로 들어왔는데 화면은
    # 파일명만 보여줘서 정상과 구분이 안 됐다. 조각 수가 그 차이를 가르는 신호다.
    size_bytes: int | None = None
    page_count: int | None = None
    n_chunks: int | None = None
    created_at: datetime


class SourceIncludeUpdate(BaseModel):
    """자료 채택/제외 토글 — 제외분은 색인·검색 근거에서 빠진다."""

    is_included: bool


class LibraryAttachRequest(BaseModel):
    """라이브러리 파일을 프로젝트 자료로 불러오기 — 대상 노드 id."""

    library_node_id: UUID


class VerifyFindingRead(BaseModel):
    """PM 검증 경고 항목 — assemble 직후 pm_verify가 저장한 문서 횡단 일관성 경고."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_number: int
    severity: str  # critical | warning
    category: str
    section_ref: str | None = None
    # 절 안정 id(sections.id) - 화면의 절 매칭·이동 정본. ref 문자열은 표시값.
    section_id: UUID | None = None
    detail: str
    created_at: datetime
    # 내용 지문 - 재검증이 행을 전량 교체해도 완료 표시가 따라오게 하는 열쇠(id는 매번 바뀐다)
    key: str = ""
    resolved: bool = False


class VerifyFindingResolve(BaseModel):
    """경고 하나의 처리 여부 토글."""

    resolved: bool


class ReportVersionRead(BaseModel):
    """보고서 버전 목록 항목 — 커밋 로그처럼 읽힌다(사유·시각·규모)."""

    version_no: int
    reason: str  # assemble | reopen | manual
    created_at: datetime
    n_sections: int
    total_chars: int


class VersionSection(BaseModel):
    """버전 스냅샷 속 절 1개 — 저장 JSONB와 같은 모양(source_ids는 응답에서 제외)."""

    section_id: str
    chapter_number: int
    section_number: int
    chapter_title: str = ""
    title: str
    content: str


class ReportVersionDetail(ReportVersionRead):
    sections: list[VersionSection]


class VersionDiffEntry(BaseModel):
    """절 하나의 버전 간 판정 — 매칭은 절 안정 id(번호가 밀려도 오판 없음)."""

    section_id: str
    status: str  # added | removed | modified | unchanged
    moved: bool = False
    base: VersionSection | None = None
    target: VersionSection | None = None


class VersionDiffResponse(BaseModel):
    base_version: int
    # None = 현재 작업 사본(sections 테이블)과 비교
    target_version: int | None = None
    n_added: int
    n_removed: int
    n_modified: int
    n_unchanged: int
    entries: list[VersionDiffEntry]


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    preset: str | None
    config: dict[str, Any]
    status: ProjectStatus
    depth_mode: DepthMode
    owner_id: UUID
    # 표시용 소유자 이름 — 라우터가 owner를 eager-load한 경우에만 채워진다
    owner_name: str | None = None
    created_at: datetime
    updated_at: datetime
    # 납품 확정 선언 시각 — completed여도 NULL이면 "검토 중"이다(0045, 완성 선언 분리).
    finalized_at: datetime | None = None
    # 저장된 본문의 총 글자 수 — 상세 조회에서만 채운다(목록에선 세지 않는다).
    # 분량이 목표에 닿았는지가 이 화면의 핵심 질문이라 헤더에 함께 보여준다.
    total_chars: int | None = None


class ManualVersionRequest(BaseModel):
    """수동 버전 저장 — note는 버전 목록에서 사람이 알아볼 짧은 꼬리표."""

    note: str | None = Field(default=None, max_length=20)


class ManualVersionResponse(BaseModel):
    # 내용이 마지막 버전과 같으면 그 번호를 그대로 돌려준다(중복 스냅샷 없음).
    version_no: int
    created: bool
