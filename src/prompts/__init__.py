"""회사 표준 프롬프트 자산 패키지 — 구성과 이관 규칙은 README.md 참고.

- components/       공용 프롬프트 조각(.md) — 작성 규칙·문체·출처·개조식·시각자료·검색
- workflow_roles/   워크플로 역할 시스템 프롬프트(.md) — Tier1·Tier2 PM·목차·검증
- agentic/analysts/ 분석 에이전트 페르소나 21종(JSON, seed-settings 원본)
- presets/          보고서 유형 프리셋 5종(JSON, seed-settings 원본)
"""

from src.prompts.loader import (
    AnalystSpec,
    PresetChapter,
    PresetSection,
    ReportPreset,
    VolumeTarget,
    list_analysts,
    list_components,
    list_presets,
    load_analyst,
    load_component,
    load_preset,
    load_workflow_role,
)

__all__ = [
    "AnalystSpec",
    "PresetChapter",
    "PresetSection",
    "ReportPreset",
    "VolumeTarget",
    "list_analysts",
    "list_components",
    "list_presets",
    "load_analyst",
    "load_component",
    "load_preset",
    "load_workflow_role",
]
