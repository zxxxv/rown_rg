"""용어 규칙 주입 — 색인이 적립한 용어(indexing/terms)를 근거팩에 맞춰 골라 싣는다.

작성기가 근거팩의 청크만 보고 전문용어를 옮기면, 문서가 다른 곳에서 정의한 용어가
일반 번역으로 후퇴한다(2026-08-27 실측: 'operational commencement'→"사업 개시" 오역
— indexing/terms 모듈 주석 참조). 색인 때 캔 정의·병기 표기 중 **이 절의 근거팩에
실제로 등장하는 것만** 골라 guidance 블록으로 싣는다 — 용어표 전체를 매 절에 실으면
토큰만 늘고 태그 주입처럼 소음이 된다.

자료 간 뭉갬 방지: 정의 항목은 출처 자료명을 달아 따로 유지한다(로더가 자료 간 병합을
하지 않는다). 같은 용어를 두 문서가 다르게 정의하면 둘 다 실리고, 헤더 지시가
"인용하는 그 자료의 정의를 따르라"로 적용 범위를 못 박는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import structlog

from src.core.types import RetrievedChunk
from src.services.indexing.terms import TERM_ENTRIES_KEY, term_key

logger = structlog.get_logger(__name__)

# 주입 상한 — 근거팩에 걸린 용어가 이보다 많으면 정의 있는 것부터 남긴다.
_MAX_INJECTED = 12
# en 표면형 대조 최소 길이 — 3자 이하 영단어는 우연 일치가 많다(약어는 별도 축).
_MIN_EN_MATCH = 4

_HEADER = (
    "용어 규칙 — 아래 용어는 근거 자료에 정의·병기 표기가 있는 것들이다. "
    "서술·번역 시 반드시 따르고, 자료마다 정의가 다르면 인용하는 그 자료의 정의를 따르라:"
)
_TAIL = (
    "위 목록에 없는 전문 용어도 뜻을 임의로 확정해 옮기지 마라 — 확신이 없으면 "
    "원어를 병기하고 문맥이 뒷받침하는 범위까지만 번역하라."
)


async def load_project_terms(project_id: UUID) -> list[dict[str, Any]]:
    """프로젝트의 채택(is_included) 자료 전체에서 용어를 모아 돌려준다.

    자료 간 병합은 하지 않는다 — 각 항목에 source_id·source_title을 달아 정의의
    적용 범위(그 자료)를 보존한다. 병합은 한 자료 안(indexing/terms)에서 끝났다.
    """
    from sqlalchemy import select

    from src.db.models.project_source import ProjectSource
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(ProjectSource.id, ProjectSource.title, ProjectSource.metadata_).where(
                    ProjectSource.project_id == project_id,
                    ProjectSource.is_included.is_(True),
                )
            )
        ).all()
    out: list[dict[str, Any]] = []
    for sid, title, meta in rows:
        for e in (meta or {}).get(TERM_ENTRIES_KEY) or []:
            if isinstance(e, dict) and (e.get("ko") or e.get("en") or e.get("abbr")):
                out.append({**e, "source_id": str(sid), "source_title": title or ""})
    return out


def _appears(e: dict[str, Any], text: str, lowered: str) -> bool:
    en = (e.get("en") or "").strip()
    if len(en) >= _MIN_EN_MATCH and en.lower() in lowered:
        return True
    abbr = (e.get("abbr") or "").strip()
    if len(abbr) >= 2 and abbr in text:  # 약어는 대소문자 일치 — EU/eu류 오탐 방지
        return True
    ko = (e.get("ko") or "").strip()
    return len(ko) >= 2 and ko in text


def _label(e: dict[str, Any]) -> str:
    en, abbr, ko = e.get("en"), e.get("abbr"), e.get("ko")
    base = en or ko or abbr or ""
    if abbr and base != abbr:
        return f"{base}({abbr})"
    return base


def _pair_clause(e: dict[str, Any]) -> str:
    """표기 지시 — ko가 있으면 한글 표기 통일, 없으면 원어 병기 유지."""
    en, abbr, ko = e.get("en"), e.get("abbr"), e.get("ko")
    if ko and (en or abbr):
        inner = ", ".join(x for x in (en, abbr) if x)
        later = abbr or ko
        return f'한글 표기는 "{ko}" — 첫 등장 시 "{ko}({inner})", 이후 "{later}"'
    if en and abbr:
        # 확립된 한글 표기가 없는 용어 — 임의 번역 대신 원어 병기를 유지시킨다.
        return f'한글 표기를 새로 만들지 마라 — 첫 등장 시 "{en}({abbr})", 이후 "{abbr}"'
    return ""


def _line(e: dict[str, Any]) -> str:
    clauses = []
    definition = e.get("definition")
    if definition:
        src = (e.get("source_title") or "").strip() or "근거 자료"
        clauses.append(f'{src}의 정의 = "{definition}" — 이 의미로 서술·번역하라')
    pair = _pair_clause(e)
    if pair:
        clauses.append(pair)
    return f"- {_label(e)}: {' · '.join(clauses)}"


def _rank(e: dict[str, Any], pack_sources: set[str]) -> tuple[int, int]:
    """정렬 — 근거팩에 든 자료의 정의 > 다른 자료의 정의 > 한영 병기 > 나머지."""
    if e.get("definition"):
        return (0 if e.get("source_id") in pack_sources else 1, 0)
    has_pair = bool(e.get("ko") and (e.get("en") or e.get("abbr")))
    return (2 if has_pair else 3, 0)


def format_term_injection(
    entries: Sequence[dict[str, Any]], chunks: Sequence[RetrievedChunk]
) -> tuple[str, list[str]]:
    """근거팩에 등장하는 용어만 골라 guidance 블록을 만든다. (블록, 용어 라벨들).

    빈 문자열이면 주입하지 않는다. 라벨 목록은 section_meta 흔적용 — 화면·채점이
    "이 절에 어떤 용어 규칙이 실렸나"를 되짚을 수 있어야 한다.
    """
    citable = [c for c in chunks if not c.is_summary]
    if not entries or not citable:
        return "", []
    text = "\n".join(c.content for c in citable)
    lowered = text.lower()
    pack_sources = {str(c.source_id) for c in citable}

    hits = [e for e in entries if _appears(e, text, lowered)]
    if not hits:
        return "", []
    hits.sort(key=lambda e: _rank(e, pack_sources))

    lines: list[str] = []
    seen_lines: set[str] = set()
    keys: list[str] = []
    for e in hits:
        line = _line(e)
        if line.endswith(": ") or line.endswith(":"):
            continue  # 정의도 병기도 없는 항목 — 지시할 내용이 없다
        if line in seen_lines:  # 같은 용어·같은 정의가 여러 자료에 있으면 한 줄만
            continue
        seen_lines.add(line)
        lines.append(line)
        keys.append(term_key(e))
        if len(lines) >= _MAX_INJECTED:
            break
    if not lines:
        return "", []
    return "\n".join([_HEADER, *lines, _TAIL]), keys
