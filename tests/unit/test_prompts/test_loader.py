"""seed-settings 이관 프롬프트 로더 검증 — 파일만 읽는 순수 로직이라 DB·LLM 없이 완결."""

from __future__ import annotations

import pytest

from src.prompts import (
    list_analysts,
    list_presets,
    load_analyst,
    load_component,
    load_preset,
    load_workflow_role,
)

COMPONENTS = [
    "agent_source_rules",
    "agent_writing_style",
    "agent_visual_rules",
]

WORKFLOW_ROLES = [
    "agent_global_system",
    "tier2_system",
    "toc_system",
    "pm_verify_system",
]


# ---------- analysts ----------


def test_list_analysts_index_order():
    # 23종 = 이관 21종 + 신설 2종(a22 성과분석·a23 입지분석, 2026-08-11 샘플 실측 반영).
    ids = [a.id for a in list_analysts()]
    assert ids == [f"a{i:02d}" for i in range(1, 24)]


def test_load_analyst_by_id_and_name():
    by_id = load_analyst("a01")
    by_name = load_analyst("STEEP분석")
    assert by_id == by_name
    assert by_id.name == "STEEP분석"


def test_analyst_fields_populated():
    for spec in list_analysts():
        assert spec.prompt.strip(), spec.id
        assert spec.desc.strip(), spec.id
        assert spec.queries, spec.id
        assert spec.volume_target is not None, spec.id
        assert spec.volume_target.min_chars < spec.volume_target.max_chars, spec.id


def test_unknown_analyst_raises():
    with pytest.raises(KeyError):
        load_analyst("없는에이전트")


# ---------- presets ----------


def test_list_presets_eight():
    names = {p.name for p in list_presets()}
    assert names == {
        "경영컨설팅보고서",
        "산업동향보고서",
        "설치운영계획보고서",
        "성과분석보고서",
        "예비타당성조사",
        "정책기획보고서",
        "조사분석보고서",
        "특화단지기획보고서",
    }


def test_preset_structure_feasibility_study():
    # 실납품 예타 실측 구조(2026-08-11) 반영: 7장, 추진 과제(5장) 확장 배분.
    preset = load_preset("예비타당성조사")
    assert len(preset.chapters) == 7
    assert sum(len(ch.sections) for ch in preset.chapters) == 34
    assert preset.domain_context


def test_preset_sections_populated():
    for preset in list_presets():
        for chapter in preset.chapters:
            assert chapter.sections, f"{preset.name}/{chapter.id}"
            for section in chapter.sections:
                assert section.title.strip(), f"{preset.name}/{chapter.id}"
                assert section.direction.strip(), f"{preset.name}/{chapter.id}"


def test_preset_agent_references_resolve():
    """프리셋 섹션이 지정한 에이전트 이름은 전부 분석 에이전트 21종 안에 있어야 한다."""
    analyst_names = {a.name for a in list_analysts()}
    for preset in list_presets():
        for chapter in preset.chapters:
            for section in chapter.sections:
                for agent_name in section.agents:
                    assert agent_name in analyst_names, (
                        f"{preset.name}/{chapter.title}/{section.title}: {agent_name}"
                    )


def test_unknown_preset_raises():
    with pytest.raises(KeyError):
        load_preset("없는프리셋")


# ---------- components / workflow_roles ----------


def test_components_load_nonempty():
    for name in COMPONENTS:
        assert load_component(name).strip(), name


def test_workflow_roles_load_nonempty():
    for name in WORKFLOW_ROLES:
        assert load_workflow_role(name).strip(), name


def test_mangled_wiki_link_restored():
    """seed 원본에서 '링크'가 위키피디아 URL로 깨진 부분이 복원됐는지."""
    for name in WORKFLOW_ROLES:
        assert "ko.wikipedia.org" not in load_workflow_role(name), name


def test_unknown_component_raises():
    with pytest.raises(KeyError):
        load_component("없는조각")
