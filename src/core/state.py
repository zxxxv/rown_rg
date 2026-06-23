from datetime import datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.clock import now
from src.core.types import (
    ProjectStage,
    SectionPlan,
    SourceRef,
    UserReviewPoint,
)


class ProjectState(BaseModel):
    """
    보고서 1건의 전체 진행 상태
    """

    # 식별
    project_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    # 입력
    topic: str
    preset: str | None = None
    options: dict = Field(default_factory=dict)

    # 단계별 출력
    sources: list[SourceRef] = Field(default_factory=list)  # 프로젝트 자료 풀
    indexed_source_ids: list[UUID] = Field(default_factory=list)  # 임베딩 완료된 자료
    section_plan: list[SectionPlan] = Field(default_factory=list)  # 섹션별 계획
    completed_section_ids: list[UUID] = Field(default_factory=list)  # 작성 완료 섹션 ID

    # 검토 게이트
    pending_review: UserReviewPoint | None = None
    review_history: list[UserReviewPoint] = Field(default_factory=list)

    # 단계
    current_stage: ProjectStage = ProjectStage.CREATED

    # 갱신 메서드
    def _touch(self, **changes) -> Self:
        return self.model_copy(update={**changes, "updated_at": now()})

    def with_stage(self, stage: ProjectStage) -> Self:
        """
        단계 전환
        """
        return self._touch(current_stage=stage)

    def add_sources(self, new_sources: list[SourceRef]) -> Self:
        """
        자료 풀에 추가
        """
        return self._touch(sources=[*self.sources, *new_sources])

    def mark_indexed(self, source_ids: list[UUID]) -> Self:
        """
        인덱싱 완료된 자료 ID 기록
        """
        return self._touch(indexed_source_ids=[*self.indexed_source_ids, *source_ids])

    def with_pending_review(self, ticket: UserReviewPoint) -> Self:
        """
        사용자 결정 대기 중인 검토 설정
        """
        return self._touch(pending_review=ticket)

    def resolve_review(self, ticket: UserReviewPoint) -> Self:
        """
        검토 게이트 결정 완료. history로 이동
        """
        if self.pending_review is None or self.pending_review.id != ticket.id:
            raise ValueError("Cannot resolve a review not currently pending")
        return self._touch(
            pending_review=None,
            review_history=[*self.review_history, ticket],
        )

    def with_section_plan(self, plan: list[SectionPlan]) -> Self:
        """
        작성 매니저가 만든 섹션 계획 설정
        """
        return self._touch(section_plan=plan)

    def mark_section_complete(self, section_id: UUID) -> Self:
        """
        섹션 작성 완료 기록
        """
        return self._touch(completed_section_ids=[*self.completed_section_ids, section_id])

    # 질의 메서드
    def is_waiting_user(self) -> bool:
        """
        사용자 결정 대기 중인지
        """
        return self.pending_review is not None

    def next_section(self) -> SectionPlan | None:
        """
        다음 작성할 섹션
        모두 완료되면 None
        """
        completed = set(self.completed_section_ids)
        for section in self.section_plan:
            if section.section_id not in completed:
                return section
        return None

    # DB 변환
    @classmethod
    def from_db(
        cls,
        project_row: dict,
        sources: list[SourceRef] | None = None,
        section_plan: list[SectionPlan] | None = None,
    ) -> Self:
        """
        DB row에서 ProjectState 복원
        """
        return cls(
            project_id=project_row["id"],
            user_id=project_row["owner_id"],
            created_at=project_row["created_at"],
            updated_at=project_row["updated_at"],
            topic=project_row["topic"],
            preset=project_row["preset"],
            options=project_row.get("config", {}),
            current_stage=ProjectStage(project_row["status"]),
            sources=sources or [],
            section_plan=section_plan or [],
        )

    def to_project_row(self) -> dict:
        """
        projects 테이블 INSERT/UPDATE용 dict
        """
        return {
            "id": self.project_id,
            "owner_id": self.user_id,
            "topic": self.topic,
            "preset": self.preset,
            "config": self.options,
            "status": self.current_stage.value,
            "updated_at": self.updated_at,
        }
