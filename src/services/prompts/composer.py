"""에이전트 프롬프트 조합·분해 — 칸으로 받아 한 장으로 만든다.

시스템 21종은 골격이 같다: `# 제목` → `## 임무` → `## 분석 방법론` → `## 핵심 산출물`.
빈 화면에 페르소나를 통째로 쓰라는 건 무리라, 폼은 이 절들을 칸으로 받고 저장할 때
여기서 한 장으로 조합한다. 조합 결과(content)가 작성 경로의 단일 진실이고, 칸 값은
재편집용으로 spec.sections에 함께 남는다.

분량은 여기 넣지 않는다 — volume_target에서 작성 시점에 생성해 붙인다
(services/generation/writer_context._volume_line). 한 값이 두 곳에 적히면 어긋난다.
"""

from __future__ import annotations

import re

# (키, 화면·프롬프트 표기) — 순서가 곧 프롬프트 순서다.
SECTION_FIELDS: tuple[tuple[str, str], ...] = (
    ("mission", "임무"),
    ("method", "분석 방법론"),
    ("deliverables", "핵심 산출물"),
)
_LABEL_TO_KEY = {label: key for key, label in SECTION_FIELDS}

# 조합할 때 붙이는 제목 줄. 다관점 절(2~3개 배정)에서 페르소나 경계 표시가 된다 —
# 없으면 여러 페르소나가 한 덩어리로 뭉갠다.
_TITLE_SUFFIX = "전문 에이전트"

_HEADING_RE = re.compile(r"^##\s*(.+?)\s*$", re.MULTILINE)


def compose_agent_prompt(name: str, sections: dict[str, str]) -> str:
    """칸 값 → 에이전트 프롬프트 한 장. 빈 칸은 절째로 생략한다."""
    parts = [f"# {name} {_TITLE_SUFFIX}"]
    for key, label in SECTION_FIELDS:
        body = (sections.get(key) or "").strip()
        if body:
            parts.append(f"## {label}\n{body}")
    extra = (sections.get("extra") or "").strip()
    if extra:
        parts.append(f"## 구성 지침\n{extra}")
    return "\n\n".join(parts) + "\n"


def parse_agent_prompt(text: str) -> dict[str, str]:
    """프롬프트 한 장 → 칸 값. '복제해서 시작'이 폼을 채울 때 쓴다.

    아는 절(임무·분석 방법론·핵심 산출물)은 각 칸으로, 나머지 절은 제목을 붙여
    extra에 모은다 — 원문에 있던 지시가 편집 과정에서 소리 없이 사라지지 않게.
    """
    out: dict[str, str] = {}
    extras: list[str] = []
    matches = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        label = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        if not body:
            continue
        key = _LABEL_TO_KEY.get(label)
        if key:
            out[key] = body
        else:
            extras.append(body if label == "구성 지침" else f"[{label}] {body}")
    if extras:
        out["extra"] = "\n\n".join(extras)
    return out
