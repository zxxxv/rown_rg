"""절 재작성(regenerate_section) — 지시문이 검색 앵커에 실리는 계약.

작성 프롬프트에만 지시문을 넣으면 절 계획 밖 지시("일본 사례 보강")에 맞는 청크가
근거 풀에 안 들어와 모델이 지시를 못 따른다(2026-08-14). 지시문은 재채점 앵커
(direction)에만 얹고, 1차 검색(제목)과 생성 프롬프트용 계획은 원본을 유지한다.
"""

from __future__ import annotations

from src.core.types import SectionDraft, SectionPlan
from src.services.sections import edit


def _plan() -> SectionPlan:
    return SectionPlan(
        chapter_number=2, section_number=2, title="국내외 정책 동향", direction="국내외 정책 비교"
    )


def _wire_fakes(monkeypatch, captured: dict):
    async def fake_split(section, chunks, **kw):
        return None  # 분할 경로는 항상 단일 호출로 폴백시킨다

    async def fake_candidates(section, chunks, **kw):
        captured["gen_direction"] = section.direction
        return [SectionDraft(section_id=section.section_id, content="본문", cited_chunk_ids=[])]

    monkeypatch.setattr(edit, "generate_section_split", fake_split)
    monkeypatch.setattr(edit, "generate_section_candidates", fake_candidates)


class TestInstructionInSearch:
    async def test_instruction_lands_in_search_anchor_only(self, monkeypatch):
        captured: dict = {}
        _wire_fakes(monkeypatch, captured)

        async def fake_retrieve(plan: SectionPlan):
            captured["search_direction"] = plan.direction
            return []

        await edit.regenerate_section(
            section=_plan(),
            retrieve=fake_retrieve,
            instruction="일본 규제 사례를 보강해줘",
            model="test-model",
        )
        # 검색 앵커에는 원 방향 + 지시문이 함께 실린다
        assert "국내외 정책 비교" in captured["search_direction"]
        assert "일본 규제 사례를 보강해줘" in captured["search_direction"]
        # 생성 프롬프트용 계획은 원본 그대로 - 지시문은 guidance로 이미 들어간다
        assert captured["gen_direction"] == "국내외 정책 비교"

    async def test_empty_instruction_keeps_plan_untouched(self, monkeypatch):
        captured: dict = {}
        _wire_fakes(monkeypatch, captured)

        async def fake_retrieve(plan: SectionPlan):
            captured["search_direction"] = plan.direction
            return []

        await edit.regenerate_section(
            section=_plan(), retrieve=fake_retrieve, instruction="  ", model="test-model"
        )
        assert captured["search_direction"] == "국내외 정책 비교"
