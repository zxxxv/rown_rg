"""회사 표준 프롬프트 로더 — n8n seed-settings에서 이관한 자산을 파일에서 읽는다.

원본: 사내 n8n_workflows/seed-settings (2026-07-21 이관).
- agentic/analysts/·presets/의 JSON은 업스트림과 diff·재동기화가 쉽도록
  파일명·내용을 원본 그대로 유지한다 (_index.json이 순서의 기준).
- components/·workflow_roles/의 .md는 system 프롬프트로 바로 조합해 쓰는 텍스트다.
- 모델별 layer3 프롬프트와 model_mapping.json은 이관하지 않았다 — 모델 분기는
  src/clients/llm (라우터·models.py)이 단일 진실이다.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

_ROOT = Path(__file__).parent
_ANALYSTS_DIR = _ROOT / "agentic" / "analysts"
_PRESETS_DIR = _ROOT / "presets"
_COMPONENTS_DIR = _ROOT / "components"
_ROLES_DIR = _ROOT / "workflow_roles"


class VolumeTarget(BaseModel):
    """분석 에이전트 산출물의 분량 목표(문자 수·페이지)."""

    min_chars: int
    max_chars: int
    pages: str


class AnalystSpec(BaseModel):
    """분석 에이전트 1종 — 섹션 작성에 쓰는 전문 페르소나 프롬프트.

    queries의 "{topic}"은 호출 시 보고서 주제로 치환하는 검색 질의 템플릿이다.
    """

    id: str
    name: str
    cat: str
    desc: str
    queries: list[str] = []
    prompt: str
    volume_target: VolumeTarget | None = None
    # 남이 만들어 공개한 개인 에이전트인지 — 파일 카탈로그·내 개인 에이전트는 False.
    # 선택 화면이 "누구 것인지"를 보여줘야 같은 이름 둘을 구분할 수 있다.
    shared: bool = False
    owner_name: str | None = None


class PresetSection(BaseModel):
    """프리셋 목차의 절 1개. agents는 AnalystSpec.name 참조."""

    title: str
    direction: str
    key_points: list[str] = []
    agents: list[str] = []


class PresetChapter(BaseModel):
    id: str
    title: str
    sections: list[PresetSection]


class ReportPreset(BaseModel):
    """보고서 유형 1종 — 챕터·섹션 골격과 섹션별 담당 분석 에이전트."""

    id: str
    name: str
    desc: str
    chapters: list[PresetChapter]
    domain_context: str = ""


def _read_markdown(directory: Path, name: str) -> str:
    path = directory / f"{name}.md"
    if not path.is_file():
        available = sorted(p.stem for p in directory.glob("*.md"))
        raise KeyError(f"프롬프트 없음: {name!r} (가능: {available})")
    return path.read_text(encoding="utf-8").strip()


def _indexed_files(directory: Path) -> list[Path]:
    """_index.json에 등록된 순서대로 파일 경로를 돌려준다."""
    index = json.loads((directory / "_index.json").read_text(encoding="utf-8"))
    return [directory / item["file"] for item in index["items"]]


def load_component(name: str) -> str:
    """공용 프롬프트 조각(components/*.md)을 읽는다. 예: "agent_writing_style"."""
    return _read_markdown(_COMPONENTS_DIR, name)


def list_components() -> list[str]:
    """공용 프롬프트 조각 이름 전체를 정렬해 돌려준다(작성 규칙 카탈로그)."""
    return sorted(p.stem for p in _COMPONENTS_DIR.glob("*.md"))


def load_workflow_role(name: str) -> str:
    """워크플로 역할 시스템 프롬프트(workflow_roles/*.md)를 읽는다. 예: "toc_system"."""
    return _read_markdown(_ROLES_DIR, name)


def catalog_file_stat(kind: str, ref: str) -> tuple[int, float] | None:
    """카탈로그 원본 파일의 (바이트 수, mtime) — 라이브러리 목록 메타 표시용.

    시스템 프롬프트는 DB 행이 아니라 저장소 파일이라 등록 시각·크기가 없었다
    (0 B·고정 날짜로 표시됨, 2026-08-09 지적). 파일 자체의 값을 그대로 보여준다.
    """
    if kind == "rule":
        path = _COMPONENTS_DIR / f"{ref}.md"
    else:
        path = next(
            (p for p in _indexed_files(_ANALYSTS_DIR) if p.stem.startswith(f"{ref}_")), None
        )
    if path is None or not path.is_file():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime


def list_analysts() -> list[AnalystSpec]:
    """분석 에이전트 전체(21종)를 _index.json 순서대로 읽는다."""
    return [
        AnalystSpec.model_validate_json(path.read_text(encoding="utf-8"))
        for path in _indexed_files(_ANALYSTS_DIR)
    ]


def load_analyst(key: str) -> AnalystSpec:
    """id("a01") 또는 name("STEEP분석")으로 분석 에이전트 1종을 찾는다."""
    analysts = list_analysts()
    for spec in analysts:
        if key in (spec.id, spec.name):
            return spec
    raise KeyError(f"분석 에이전트 없음: {key!r} (가능: {[a.name for a in analysts]})")


def list_presets() -> list[ReportPreset]:
    """보고서 유형 프리셋 전체(5종)를 _index.json 순서대로 읽는다."""
    return [
        ReportPreset.model_validate_json(path.read_text(encoding="utf-8"))
        for path in _indexed_files(_PRESETS_DIR)
    ]


def load_preset(key: str) -> ReportPreset:
    """id 또는 name("예비타당성조사" 등)으로 프리셋 1종을 찾는다."""
    presets = list_presets()
    for preset in presets:
        if key in (preset.id, preset.name):
            return preset
    raise KeyError(f"프리셋 없음: {key!r} (가능: {[p.name for p in presets]})")
