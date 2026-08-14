from datetime import datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.clock import now
from src.core.types import (
    ProjectStage,
    SectionCandidateSet,
    SectionDraft,
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
    # 보고서 제목 — 표지에 쓰인다. topic은 "무엇을 검토한다"는 긴 지시문이라 표지에
    # 그대로 올리면 문장이 제목 자리에 박힌다(2026-08-09 실사용 지적). 없으면 topic 폴백.
    title: str = ""
    # 표지 작성자 표기 — 소유자 이름. 실행 상태가 아니라 렌더 입력이라 조회 시 채운다.
    author: str = ""
    preset: str | None = None
    depth_mode: str = "full_report"  # 작성 깊이 — RAPTOR 트리 깊이 등 품질 노브의 입력
    options: dict = Field(default_factory=dict)

    # 단계별 출력
    sources: list[SourceRef] = Field(default_factory=list)  # 프로젝트 자료 풀
    indexed_source_ids: list[UUID] = Field(default_factory=list)  # 임베딩 완료된 자료
    section_plan: list[SectionPlan] = Field(default_factory=list)  # 섹션별 계획
    completed_section_ids: list[UUID] = Field(default_factory=list)  # 작성 완료 섹션 ID

    # 설계 브리프(게이트 payload) — plan_brief 단계가 만든다. 게이트 함수는 순수라
    # DB(개인 에이전트 카탈로그)를 못 보므로, async 단계가 여기 실어 나른다.
    # 영속화 대상 아님(to_project_row에 없음) — 게이트 payload로 review_points에 남는다.
    design_brief: dict | None = None

    # 절별 생성 지표 — section_id → {evidence_count, volume_scaled, ...}. sections.meta로
    # 영속화돼 화면이 '자료 부족' 배지를 띄운다(본문에 메타 서술을 넣지 않기 위한 통로).
    section_meta: dict[UUID, dict] = Field(default_factory=dict)

    # QA 후보 선택 (write 스테이지가 적재, QA_SELECT 게이트에서 사람이 고름)
    section_candidates: list[SectionCandidateSet] = Field(default_factory=list)
    section_selections: dict[UUID, UUID] = Field(default_factory=dict)  # section_id → candidate_id

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

    def with_section_meta(self, meta: dict[UUID, dict]) -> Self:
        """절별 생성 지표 병합 — 부분 실행(증분 저장)에서도 앞선 절의 지표를 잃지 않는다."""
        return self._touch(section_meta={**self.section_meta, **meta})

    def with_section_candidates(self, candidate_sets: list[SectionCandidateSet]) -> Self:
        """
        섹션별 후보+정적검사 결과 적재 (write 스테이지 산출)
        """
        return self._touch(section_candidates=candidate_sets)

    def record_selection(self, section_id: UUID, candidate_id: UUID) -> Self:
        """
        사람이 고른 섹션 후보 기록 (QA_SELECT 게이트 결정)
        """
        return self._touch(section_selections={**self.section_selections, section_id: candidate_id})

    def selected_drafts(self) -> list[SectionDraft]:
        """
        선택된 후보의 draft를 section_plan 순서대로 반환. 미선택·미존재 섹션은 건너뛴다.
        """
        by_section = {cs.section_id: cs for cs in self.section_candidates}
        drafts: list[SectionDraft] = []
        for section in self.section_plan:
            chosen = self.section_selections.get(section.section_id)
            cset = by_section.get(section.section_id)
            if chosen is None or cset is None:
                continue
            for cand in cset.candidates:
                if cand.candidate_id == chosen:
                    drafts.append(cand.draft)
                    break
        return drafts

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
        """DB row에서 ProjectState 복원.

        section_plan을 안 넘기면 config의 정본(``_section_plan``)에서 되살린다 —
        plan 복원 경로가 여기 하나로 모이므로 호출부마다 게이트 payload를 뒤지거나
        목차 위치 대응으로 재구성할 필요가 없다(core.section_plan 참조).
        """
        from src.core.section_plan import plan_from_config

        config = project_row.get("config", {})
        return cls(
            project_id=project_row["id"],
            user_id=project_row["owner_id"],
            created_at=project_row["created_at"],
            updated_at=project_row["updated_at"],
            topic=project_row["topic"],
            title=project_row.get("title") or "",
            preset=project_row["preset"],
            depth_mode=project_row.get("depth_mode") or "full_report",
            options=config,
            current_stage=ProjectStage(project_row["status"]),
            sources=sources or [],
            section_plan=section_plan or plan_from_config(config),
        )

    def to_project_row(self) -> dict:
        """projects 테이블 INSERT/UPDATE용 dict.

        config에는 section_plan 정본을 함께 싣는다 — from_db가 거기서 되살리므로
        둘이 어긋나면 복원이 조용히 옛 plan을 집는다.
        """
        from src.core.section_plan import config_with_plan

        return {
            "id": self.project_id,
            "owner_id": self.user_id,
            "topic": self.topic,
            "preset": self.preset,
            "config": config_with_plan(self.options, self.section_plan),
            "status": self.current_stage.value,
            "updated_at": self.updated_at,
        }
