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

from collections import Counter, defaultdict
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
# 접미 병합 최소 길이 — qa/term_notation._MIN_ABSORB_CHARS와 같은 눈금(짧은 접미가
# 전부를 삼키는 것 방지). 두 쪽이 다른 눈금을 쓰면 주입은 상충으로 빼는데 검사기는
# 정상으로 보는(또는 그 반대) 어긋남이 생긴다.
_MIN_ABSORB_CHARS = 4

_HEADER = (
    "용어 규칙 — 아래 용어는 근거 자료에 정의·병기 표기가 있는 것들이다. "
    "서술·번역 시 반드시 따르고, 자료마다 정의가 다르면 인용하는 그 자료의 정의를 따르라:"
)
_TAIL = (
    "위 목록에 없는 전문 용어도 뜻을 임의로 확정해 옮기지 마라 — 확신이 없으면 "
    "원어를 병기하고 문맥이 뒷받침하는 범위까지만 번역하라."
)


GLOSSARY_ORIGIN = "glossary"
GLOSSARY_TITLE = "정본 용어집"


def normalize_term_key(en: str | None, abbr: str | None) -> str:
    """정본 용어집·상충 판정이 공유하는 원어 키 — en 우선, 공백 정규화·소문자."""
    raw = str(en or abbr or "")
    return " ".join(raw.split()).lower()


def glossary_row_to_entry(row: Any) -> dict[str, Any]:
    """GlossaryTerm 행 → 채굴 엔트리와 같은 모양. origin으로 정본임을 표시한다."""
    return {
        "ko": row.ko,
        "en": row.en,
        "abbr": row.abbr,
        "definition": row.definition,
        "context": row.note,
        "origin": GLOSSARY_ORIGIN,
        "source_id": None,
        "source_title": GLOSSARY_TITLE,
    }


async def load_glossary_entries(project_id: UUID) -> list[dict[str, Any]]:
    """정본 용어집(회사 공유 + 이 프로젝트 덮어쓰기) — 같은 키면 프로젝트 행이 이긴다.

    테이블이 아직 없어도(마이그레이션 전) 용어 주입 전체가 죽으면 안 되므로 실패는
    빈 목록으로 눕는다 — 정본 없이도 채굴 용어표는 성립한다.
    """
    from sqlalchemy import select

    from src.db.models.glossary_term import GlossaryTerm
    from src.db.session import open_session

    try:
        async with open_session() as session:
            rows = (
                (
                    await session.execute(
                        select(GlossaryTerm).where(
                            (GlossaryTerm.project_id.is_(None))
                            | (GlossaryTerm.project_id == project_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
    except Exception:
        logger.warning("glossary_load_failed", project_id=str(project_id), exc_info=True)
        return []
    by_key: dict[str, Any] = {}
    for row in rows:  # 회사 공유 먼저 깔고 프로젝트 행으로 덮는다
        if row.project_id is None:
            by_key.setdefault(row.term_key, row)
    for row in rows:
        if row.project_id is not None:
            by_key[row.term_key] = row
    return [glossary_row_to_entry(r) for r in by_key.values()]


async def load_project_terms(project_id: UUID) -> list[dict[str, Any]]:
    """정본 용어집 + 프로젝트의 채택(is_included) 자료 전체의 채굴 용어.

    자료 간 병합은 하지 않는다 — 각 항목에 source_id·source_title을 달아 정의의
    적용 범위(그 자료)를 보존한다. 병합은 한 자료 안(indexing/terms)에서 끝났다.
    정본(사람 확정)이 목록 앞에 서고, 주입·검사가 origin으로 우선 취급한다.
    """
    from sqlalchemy import select

    from src.db.models.project_source import ProjectSource
    from src.db.session import open_session

    async with open_session() as session:
        rows = (
            await session.execute(
                select(ProjectSource.id, ProjectSource.title, ProjectSource.metadata_).where(
                    ProjectSource.project_id == project_id,
                    ProjectSource.is_included.is_(True),
                )
            )
        ).all()
    out: list[dict[str, Any]] = await load_glossary_entries(project_id)
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


def _pair_conflict_key(e: dict[str, Any]) -> str | None:
    """한영 병기 항목의 원어 키 — 병기가 아니면 None."""
    if not (e.get("ko") and (e.get("en") or e.get("abbr"))):
        return None
    raw = str(e.get("en") or e.get("abbr"))
    return " ".join(raw.split()).lower()


def _absorb_bares(counter: Counter[str]) -> tuple[dict[str, str], Counter[str]]:
    """공백 제거 표기들을 접미 관계로 병합 — (원표기→대표, 대표별 표수)."""
    reps: list[str] = []
    remap: dict[str, str] = {}
    for bare in sorted(counter, key=len):
        rep = next(
            (r for r in reps if bare == r or (len(r) >= _MIN_ABSORB_CHARS and bare.endswith(r))),
            None,
        )
        if rep is None:
            reps.append(bare)
            rep = bare
        remap[bare] = rep
    merged: Counter[str] = Counter()
    for bare, n in counter.items():
        merged[remap[bare]] += n
    return remap, merged


def _pair_conflicts(
    entries: Sequence[dict[str, Any]],
) -> dict[str, tuple[dict[str, str], str | None]]:
    """자료 간 같은 원어의 한글 표기가 갈린 키 → (표기→대표 지도, 승자 대표|None).

    상충 표기를 그대로 주입하면 채굴 오염이 본문 전체로 증폭된다(2026-08-28 v7 실측:
    무역협회 자료의 문장 조각 "재생에너지 사용 확인(RE100)"이 주입을 타고 3.4·4.1절
    본문 병기로 번졌다). 승자는 다수결 — 2표 이상이면서 모든 경쟁 표기보다 많을 때만
    인정하고, 동률이면 승자 없음(그 키의 표기 강제는 통째로 뺀다).
    """
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for e in entries:
        key = _pair_conflict_key(e)
        if key:
            votes[key][str(e["ko"]).replace(" ", "")] += 1
    out: dict[str, tuple[dict[str, str], str | None]] = {}
    for key, counter in votes.items():
        remap, merged = _absorb_bares(counter)
        if len(merged) < 2:
            continue
        top = merged.most_common(2)
        winner = top[0][0] if top[0][1] >= 2 and top[0][1] > top[1][1] else None
        out[key] = (remap, winner)
    return out


def _rank(e: dict[str, Any], pack_sources: set[str]) -> tuple[int, int]:
    """정렬 — 정본 > 근거팩에 든 자료의 정의 > 다른 자료의 정의 > 한영 병기 > 나머지."""
    if e.get("origin") == GLOSSARY_ORIGIN:
        return (-1, 0)  # 사람이 확정한 표기가 캡에 밀려 떨어지면 승격이 무의미하다
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

    # 정본 우선 — 사람이 확정한 표기가 있으면 그 키의 상충은 이미 판가름 났다.
    # 다른 표기의 채굴 병기는 강제에서 떨구고(정의는 유지) 정본만 표기를 지시한다.
    glossary_ko: dict[str, str] = {}
    for e in entries:
        if e.get("origin") == GLOSSARY_ORIGIN:
            key = _pair_conflict_key(e)
            if key:
                glossary_ko.setdefault(key, str(e["ko"]).replace(" ", ""))

    # 상충 보수화 — 근거팩 밖 자료의 표까지 전체 용어표로 세야 상충이 보인다(팩에는
    # 오염판 하나만 걸릴 수 있다). 승자 표기만 강제하고, 승자가 없으면 표기 강제를
    # 통째로 뺀다(정의 항목은 정의만 남긴다). 정본이 있는 키는 다수결 대상이 아니다.
    conflicts = _pair_conflicts([e for e in entries if e.get("origin") != GLOSSARY_ORIGIN])
    if conflicts or glossary_ko:
        adjusted: list[dict[str, Any]] = []
        for e in hits:
            if e.get("origin") == GLOSSARY_ORIGIN:
                adjusted.append(e)
                continue
            key = _pair_conflict_key(e)
            if key in glossary_ko:
                if str(e.get("ko") or "").replace(" ", "") == glossary_ko[key]:
                    adjusted.append(e)
                elif e.get("definition"):
                    adjusted.append({**e, "ko": None})
                else:
                    logger.info(
                        "term_injection_overridden_by_glossary",
                        term=_label(e),
                        ko=e.get("ko"),
                        source=e.get("source_title"),
                    )
                continue
            if key is None or key not in conflicts:
                adjusted.append(e)
                continue
            remap, winner = conflicts[key]
            bare = str(e["ko"]).replace(" ", "")
            if winner is not None and remap.get(bare) == winner:
                adjusted.append(e)
            elif e.get("definition"):
                adjusted.append({**e, "ko": None})
            else:
                logger.info(
                    "term_injection_conflict_dropped",
                    term=_label(e),
                    ko=e.get("ko"),
                    source=e.get("source_title"),
                )
        hits = adjusted
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
