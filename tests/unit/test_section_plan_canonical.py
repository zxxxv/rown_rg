"""section_plan 정본화 — projects.config["_section_plan"]이 목차 표현의 단일 진실.

plan이 게이트 payload에만 살던 시절, 목차 정보가 세 벌(config.outline / 게이트 payload /
sections 행)로 갈라져 서로 다른 것을 알고 있었다. 실제로 새고 있었다(2026-08-14 운영 실측):
sections.chapter_title이 프리셋 파일에서 와서, 프리셋 없는 프로젝트는 전부 'N장'이고
장을 더한 프로젝트는 6장에 프리셋 6번째 제목이 붙어 있었다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.api.routers.projects import merge_config_update
from src.core.section_plan import (
    SECTION_PLAN_KEY,
    config_with_plan,
    dump_section_plan,
    load_section_plan,
    plan_from_config,
)
from src.core.state import ProjectState
from src.core.types import SectionPlan
from src.services.sections.store import _chapter_titles

# section_id는 고정한다 — 호출마다 새로 만들면 왕복 비교가 ID 때문에 실패한다.
_ID1 = UUID("11111111-1111-4111-8111-111111111111")
_ID2 = UUID("22222222-2222-4222-8222-222222222222")


def _plan() -> list[SectionPlan]:
    return [
        SectionPlan(
            section_id=_ID1,
            chapter_number=1,
            section_number=1,
            title="개요",
            chapter_title="글로벌 RE100",
            direction="글로벌 RE100의 일반현황을 제시",
            key_points=["참여 기업 수", "이행 수단"],
            analysts=["탄소규제동향분석"],
            search_queries=["RE100 참여 현황 2024"],
            builds_on=["1.1", "2.*"],
        ),
        SectionPlan(
            section_id=_ID2,
            chapter_number=2,
            section_number=1,
            title="개요",
            chapter_title="EU CBAM",
        ),
    ]


class TestRoundTrip:
    def test_모든_필드가_왕복에서_살아남는다(self) -> None:
        plan = _plan()
        restored = load_section_plan(dump_section_plan(plan))
        assert restored == plan

    def test_같은_절_제목이라도_장_제목으로_구별된다(self) -> None:
        restored = load_section_plan(dump_section_plan(_plan()))
        assert [s.chapter_title for s in restored] == ["글로벌 RE100", "EU CBAM"]

    def test_구버전_항목은_장_제목_없이_복원된다(self) -> None:
        """chapter_title이 없던 payload — 크래시가 아니라 옛 동작으로 내려앉는다."""
        old = [
            {
                "section_id": str(uuid4()),
                "chapter_number": 1,
                "section_number": 1,
                "title": "개요",
            }
        ]
        assert load_section_plan(old)[0].chapter_title == ""

    def test_깨진_항목은_건너뛴다(self) -> None:
        good = dump_section_plan(_plan()[:1])
        assert len(load_section_plan([*good, {"title": "번호 없음"}, "문자열", None])) == 1

    def test_리스트가_아니면_빈_목록(self) -> None:
        assert load_section_plan(None) == []
        assert load_section_plan({"chapters": []}) == []


class TestConfigCarrier:
    def test_config에서_plan을_읽는다(self) -> None:
        config = config_with_plan({"outline": {"chapters": []}}, _plan())
        assert plan_from_config(config) == _plan()

    def test_정본이_없으면_빈_목록(self) -> None:
        assert plan_from_config({"outline": {}}) == []
        assert plan_from_config(None) == []

    def test_항상_새_dict를_돌려준다(self) -> None:
        """JSONB in-place 수정은 SQLAlchemy가 dirty로 안 잡아 커밋해도 안 써진다.

        API 모양으로 못 박은 계약 — 호출부는 반환값을 재할당할 수밖에 없다.
        """
        original = {"outline": {"chapters": []}}
        updated = config_with_plan(original, _plan())
        assert updated is not original
        assert SECTION_PLAN_KEY not in original

    def test_plan이_비면_키를_지운다(self) -> None:
        config = config_with_plan({}, _plan())
        assert SECTION_PLAN_KEY not in config_with_plan(config, [])

    def test_사용자_옵션은_보존된다(self) -> None:
        config = config_with_plan({"search_scope": "global", "outline": {}}, _plan())
        assert config["search_scope"] == "global"


class TestStateRestore:
    def _row(self, config: dict) -> dict:
        now = datetime.now(UTC)
        return {
            "id": uuid4(),
            "owner_id": uuid4(),
            "created_at": now,
            "updated_at": now,
            "topic": "글로벌 탄소규제의 도입 및 적용 동향",
            "title": "탄소규제",
            "preset": None,
            "depth_mode": "full_report",
            "status": "indexing",
            "config": config,
        }

    def test_from_db가_config에서_plan을_되살린다(self) -> None:
        state = ProjectState.from_db(self._row(config_with_plan({}, _plan())))
        assert state.section_plan == _plan()

    def test_명시_인자가_config보다_우선한다(self) -> None:
        """복원 경로가 겹칠 때(구 게이트 payload) 호출부 결정을 존중한다."""
        other = [SectionPlan(chapter_number=9, section_number=9, title="다른 절")]
        state = ProjectState.from_db(self._row(config_with_plan({}, _plan())), None, other)
        assert state.section_plan == other

    def test_정본이_없으면_빈_plan(self) -> None:
        assert ProjectState.from_db(self._row({})).section_plan == []

    def test_to_project_row가_plan을_싣는다(self) -> None:
        state = ProjectState.from_db(self._row(config_with_plan({}, _plan())))
        assert plan_from_config(state.to_project_row()["config"]) == _plan()


class TestOutlineEditInvalidatesPlan:
    """목차를 고치면 파생 스냅샷은 버려야 한다 — 안 그러면 옛 목차로 실행된다."""

    def test_목차가_바뀌면_정본을_버린다(self) -> None:
        current = config_with_plan({"outline": {"chapters": [{"title": "1장"}]}}, _plan())
        merged = merge_config_update(current, {"outline": {"chapters": [{"title": "바뀐 장"}]}})
        assert SECTION_PLAN_KEY not in merged

    def test_목차가_그대로면_정본을_지킨다(self) -> None:
        outline = {"chapters": [{"title": "1장"}]}
        current = config_with_plan({"outline": outline}, _plan())
        merged = merge_config_update(current, {"outline": outline, "search_scope": "global"})
        assert plan_from_config(merged) == _plan()

    def test_클라이언트가_정본을_덮어쓰지_못한다(self) -> None:
        outline = {"chapters": [{"title": "1장"}]}
        current = config_with_plan({"outline": outline}, _plan())
        merged = merge_config_update(current, {"outline": outline, SECTION_PLAN_KEY: []})
        assert plan_from_config(merged) == _plan()


class TestDesignPlanInjection:
    """승인된 실행 계획(config._design_plan) → 작성 guidance. 계획이 계약이 되는 지점."""

    def _state(self, design_plan: dict | None) -> ProjectState:
        options = {"_design_plan": design_plan} if design_plan is not None else {}
        return ProjectState(
            project_id=uuid4(),
            user_id=uuid4(),
            topic="주제",
            section_plan=_plan(),
            options=options,
        )

    def test_승인된_계획이_guidance_블록이_된다(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state(
            {
                str(_ID1): {
                    "goal": "참여 현황 제시",
                    "source_strategy": "연차보고서",
                    "writing_plan": "현황→시사점",
                }
            }
        )
        note = design_plan_note(state, state.section_plan[0])
        assert "승인된" in note
        assert "- 목표: 참여 현황 제시" in note
        assert "- 자료 활용: 연차보고서" in note
        assert "- 구성: 현황→시사점" in note

    def test_계획_없는_절은_빈_문자열(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state({str(_ID1): {"goal": "x"}})
        assert design_plan_note(state, state.section_plan[1]) == ""

    def test_계획이_아예_없으면_빈_문자열(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state(None)
        assert design_plan_note(state, state.section_plan[0]) == ""

    def test_빈_필드만_있으면_빈_문자열(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state({str(_ID1): {"goal": "", "source_strategy": " "}})
        assert design_plan_note(state, state.section_plan[0]) == ""

    def test_목차가_바뀌면_계획도_버려진다(self) -> None:
        """merge_config_update — 옛 목차 계획이 새 목차 절에 주입되는 어긋남 방지."""
        current = {
            "outline": {"chapters": [{"title": "1장"}]},
            "_design_plan": {str(_ID1): {"goal": "x"}},
        }
        merged = merge_config_update(current, {"outline": {"chapters": [{"title": "바뀐"}]}})
        assert "_design_plan" not in merged

    def test_목차가_그대로면_계획을_지킨다(self) -> None:
        outline = {"chapters": [{"title": "1장"}]}
        current = {"outline": outline, "_design_plan": {str(_ID1): {"goal": "x"}}}
        merged = merge_config_update(current, {"outline": outline})
        assert merged["_design_plan"] == {str(_ID1): {"goal": "x"}}


class TestChapterTitlePersisted:
    """sections.chapter_title이 프리셋이 아니라 실제 목차를 따르는가(2026-08-14 버그)."""

    def _state(self, preset: str | None) -> ProjectState:
        return ProjectState(
            project_id=uuid4(),
            user_id=uuid4(),
            topic="주제",
            preset=preset,
            section_plan=_plan(),
        )

    def test_프리셋이_없어도_장_제목이_저장된다(self) -> None:
        """탄소규제 런은 이것 때문에 sections가 전부 '1장'~'4장'이었다."""
        assert _chapter_titles(self._state(None)) == {1: "글로벌 RE100", 2: "EU CBAM"}

    def test_프리셋보다_사용자_목차가_우선한다(self) -> None:
        """사용자가 목차 화면에서 고친 장 제목이 프리셋 원본에 덮이면 안 된다."""
        titles = _chapter_titles(self._state("조사분석보고서"))
        assert titles[1] == "글로벌 RE100"
        assert titles[2] == "EU CBAM"

    def test_장_제목이_없는_옛_plan은_폴백(self) -> None:
        state = self._state(None).model_copy(
            update={"section_plan": [SectionPlan(chapter_number=3, section_number=1, title="개요")]}
        )
        assert _chapter_titles(state) == {3: "3장"}


class TestOwnershipArcInjection:
    """소유권·아크 필드 렌더링 - 4차 실측(절 간 중복 463문장)의 처방이 프롬프트에 닿는 지점."""

    def _state(self, design_plan: dict | None) -> ProjectState:
        options = {"_design_plan": design_plan} if design_plan is not None else {}
        return ProjectState(
            project_id=uuid4(),
            user_id=uuid4(),
            topic="주제",
            section_plan=_plan(),
            options=options,
        )

    def test_소유_토픽과_금지_토픽이_렌더된다(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state(
            {
                str(_ID1): {
                    "goal": "g",
                    "owns": "RE100 실태조사",
                    "foreign_topics": "CBAM 제도 연혁(2.1절 소관)",
                }
            }
        )
        note = design_plan_note(state, state.section_plan[0])
        assert "정본으로 서술할 토픽: RE100 실태조사" in note
        assert "재서술 금지" in note
        assert "CBAM 제도 연혁(2.1절 소관)" in note
        assert "확정된 값으로 주입된 수치를 인용하는 것은 허용" in note

    def test_아크_한_줄이_렌더된다(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state(
            {
                str(_ID1): {
                    "goal": "g",
                    "receives": "1.1절이 참여 기준 정의를 다룬다",
                    "establishes": "총부담 추정 → 4.5절이 받는다",
                }
            }
        )
        note = design_plan_note(state, state.section_plan[0])
        assert "이어받는 전제: 1.1절이 참여 기준 정의를 다룬다" in note
        assert "세워 넘길 것: 총부담 추정" in note

    def test_소유권_필드가_비면_기존_렌더와_동일(self) -> None:
        from src.workflows.write_loop import design_plan_note

        state = self._state(
            {str(_ID1): {"goal": "g", "owns": "", "foreign_topics": "", "receives": ""}}
        )
        note = design_plan_note(state, state.section_plan[0])
        assert note == "설계 검토에서 승인된 이 절의 실행 계획:\n- 목표: g"
