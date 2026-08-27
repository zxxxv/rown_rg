"""자료 용어 채굴 — 문서가 스스로 정의한 용어·한영 병기 표기를 색인 때 적립.

병리(2026-08-27 실측, v6 런 1.1절): RE100 가이던스가 p.20에서 정의한
'operational commencement'(= supply arrangement start year, 공급계약 개시)가
작성 근거팩에는 정의 없이 쓰인 p.12 청크만 들어갔고, 작성기는 통계적 우세 번역
("사업 개시")으로 후퇴했다 — 설비 준공(commissioning)과 혼동되는 표기. 정의가
있는 자리와 용어가 쓰인 자리를 청킹이 갈라놓는 구조 문제라, 색인 때 문서 전체를
훑어 project_sources.metadata_[TERM_ENTRIES_KEY]에 적립해 두고, 작성 시 근거팩에
등장하는 용어만 골라 프롬프트에 주입한다(services/generation/term_rules 참조).

채굴은 2단(2026-08-27 사용자 결정): 결정적 패턴(공짜) + LLM 보강 1콜(최저가 모델).
- 정의는 자료 단위로만 적립하고 자료 간 병합하지 않는다 — 같은 용어를 문서마다
  다르게 규정할 수 있어, 합치면 남의 정의가 엉뚱한 자료의 서술에 적용된다.
- LLM이 내놓은 definition은 본문 실재 검사를 통과해야 남는다 — 상식으로 지어낸
  정의가 끼면 근거사슬 밖 지식이 프롬프트로 새는 통로가 된다.
- 실패는 비치명 — 용어 없이도 색인은 성공이다(qa/span_vectors.store_quietly 계약).
- 같은 본문 재색인은 해시로 건너뛴다(LLM 콜 중복 방지).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import UUID

import structlog

from src.core.config import settings

logger = structlog.get_logger(__name__)

TERM_ENTRIES_KEY = "term_entries"
TERM_MINING_KEY = "term_mining"

_MAX_TERMS_PER_SOURCE = 80
_MAX_DEF_CHARS = 240
_MAX_KO_CHARS = 60
_MAX_EN_CHARS = 80
_MAX_ABBR_CHARS = 20

# LLM 입력 캡 — 정의·용어집은 문서 앞(서두·용어 정리)이나 뒤(부록)에 몰린다.
# 넘치면 머리+꼬리만 보낸다(최저가 모델이라 비용은 미미, 캡은 폭주 방어).
_LLM_INPUT_HEAD = 80_000
_LLM_INPUT_TAIL = 40_000
_LLM_MIN_INPUT = 500  # 이보다 짧은 본문은 패턴만으로 충분하다
_LLM_MAX_TOKENS = 3_000

# 풀네임 병기가 생략되는 일반 상식 약어 — 용어표에 안 담는다(export/report와 같은 목록).
_COMMON_ABBR: frozenset[str] = frozenset({"AI", "IT", "API", "GDP", "UN", "EU", "US", "UK", "OECD"})

# ── 결정적 패턴 ──────────────────────────────────────────────────────────────

# 한글 용어(영문 …[, 약어]) 병기 — 한글 구 1~4어절, 선행 어절 2자 이상(1자 조사 배제),
# 괄호 앞 공백 불허("비율 (B/C)" 같은 비약어 괄호 배제). export/report._ABBR_RE의
# 어절 규칙을 따르되 괄호 안은 영문 전체(쉼표로 약어 병기 허용)를 받는다.
_KO_PAREN_RE = re.compile(
    r"(?P<ko>(?:[가-힣][가-힣A-Za-z0-9]+ ){0,3}[가-힣][가-힣A-Za-z0-9]*)"
    r"\((?P<inner>[A-Za-z][^)\n가-힣]{2,90})\)"
)

# 영문 풀네임 (ABBR) — 풀네임은 두 단어 이상(한 단어는 일반 낱말과 구분이 안 된다).
_EN_ABBR_RE = re.compile(
    r"(?P<en>[A-Z][A-Za-z0-9.&\-]*(?:\s+[A-Za-z0-9.&/\-]+)+)\s?\((?P<abbr>[A-Z][A-Za-z0-9&/\-]{1,15})\)"
)

# 정의 문형(영문): 'X' means/refers to/is defined as …
_QUOTED_DEF_RE = re.compile(
    r"['\"‘“](?P<term>[^'\"’”\n]{3,60})['\"’”]\s+"
    r"(?:means|refers to|is defined as)\b",
    re.I,
)
_IS_DEFINED_RE = re.compile(
    r"(?P<term>[A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z0-9\-]+){0,5})\s+is defined as\b", re.I
)
# "A - equivalent to B" — 정의 대상은 B다(RE100 실측형: "Supply arrangement start
# year - equivalent to operational commencement and must be …").
_EQUIV_RE = re.compile(
    r"\bequivalent to\s+['\"‘“]?(?P<term>[A-Za-z][A-Za-z0-9\-]*(?:\s+[A-Za-z0-9\-]+){0,4})"
)
# 용어 캡처 끝에 딸려 온 접속·조동사 꼬리를 걷어낸다("operational commencement and must be").
_TERM_TRIM_WORDS = frozenset(
    "and or which that must shall may is are was were the a an of to in on for with "
    "before after be".split()
)

# 풀네임으로 삼을 수 없는 문장 조각 — 조사·연결어미가 섞였다면 괄호 앞 문맥을 긁어 온
# 것이다(export/report._SENTENCE_FRAGMENT_RE와 같은 함정: "…동시에 잉카(Ingka)").
# 거기서는 빈 값으로 두지만, 여기서는 마지막 조각 이후만 남긴다(뒷어절이 진짜 용어다).
_KO_FRAGMENT_RE = re.compile(
    r"(?:[을를이가은는와과에의로]|으로|에서|하는|되는|같은|또는|동시에|위한|따른)\s"
)

# 정의 문형(한글): "X란/이란/라 함은 …을 말한다/의미한다" — 법령·제도 문서의 정의 서명.
# 정의의 '란/이란'은 명사에 **직접** 붙는다("온실가스란") — 용어가 공백으로 끝나지
# 못하게 해 "미국과 이란(Iran) …라고 말한다"류 국가명 오탐을 배제한다.
_KO_DEF_RE = re.compile(
    r"(?:^|(?<=[\s(\"'‘“]))"
    r"[\"'‘“]?(?P<term>[가-힣A-Za-z0-9][가-힣A-Za-z0-9·\- ]{0,28}?[가-힣A-Za-z0-9])[\"'’”]?"
    r"(?:이란|란|라 함은)\s+(?P<body>[^.\n]{5,220}?(?:말한다|의미한다|뜻한다|가리킨다))"
)


def _looks_abbr(token: str) -> bool:
    """약어다운 토큰인가 — 공백 없고 소문자 둘 이상이면 영어 낱말로 본다(IoT·PhD 허용)."""
    return (
        " " not in token
        and 2 <= len(token) <= _MAX_ABBR_CHARS
        and sum(1 for ch in token if ch.islower()) <= 1
    )


def _trim_term_tail(term: str) -> str:
    words = term.split()
    while words and words[-1].lower() in _TERM_TRIM_WORDS:
        words.pop()
    return " ".join(words)


def _sentence_around(text: str, start: int, end: int) -> str:
    """매치를 품은 문장 하나 — 정의는 요약하지 않고 원문 그대로 싣는다(캡 240자)."""
    nl = text.rfind("\n", 0, start)
    dot = text.rfind(". ", 0, start)
    begin = max(nl + 1, dot + 2 if dot != -1 else 0, 0)
    stops = [i for i in (text.find("\n", end), text.find(". ", end)) if i != -1]
    if stops:
        finish = min(stops)
        if text[finish] == ".":
            finish += 1
    else:
        finish = len(text)
    sentence = " ".join(text[begin:finish].split())
    if len(sentence) > _MAX_DEF_CHARS:
        sentence = sentence[: _MAX_DEF_CHARS - 1] + "…"
    return sentence


def _entry(
    *,
    ko: str | None = None,
    en: str | None = None,
    abbr: str | None = None,
    definition: str | None = None,
    origin: str = "pattern",
) -> dict[str, Any]:
    return {
        "ko": (ko or "").strip()[:_MAX_KO_CHARS] or None,
        "en": (en or "").strip()[:_MAX_EN_CHARS] or None,
        "abbr": (abbr or "").strip()[:_MAX_ABBR_CHARS] or None,
        "definition": (definition or "").strip()[:_MAX_DEF_CHARS] or None,
        "origin": origin,
    }


def _identifiers(e: dict[str, Any]) -> list[str]:
    ids = []
    if e.get("en"):
        ids.append(f"en:{e['en'].lower()}")
    if e.get("abbr"):
        ids.append(f"ab:{e['abbr'].upper()}")
    if e.get("ko"):
        ids.append(f"ko:{e['ko']}")
    return ids


def term_key(e: dict[str, Any]) -> str:
    """대표 식별자 — 화면·meta 흔적용 짧은 라벨."""
    return e.get("en") or e.get("abbr") or e.get("ko") or ""


def merge_entries(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한 자료 안에서의 병합 — 같은 용어(en/abbr/ko 어느 축이든 겹침)는 칸을 채워 합친다.

    자료 **간** 병합은 하지 않는다(모듈 주석 참조) — 이 함수는 한 문서의 패턴·LLM
    산출을 합칠 때만 쓴다. definition 충돌은 먼저 온 쪽(패턴)이 이긴다.
    """
    out: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for group in groups:
        for e in group:
            found = next((index[i] for i in _identifiers(e) if i in index), None)
            if found is None:
                if len(out) >= _MAX_TERMS_PER_SOURCE:
                    continue
                out.append(e)
            else:
                for field in ("ko", "en", "abbr", "definition"):
                    if not found.get(field) and e.get(field):
                        found[field] = e[field]
                e = found
            for i in _identifiers(e):
                index[i] = e
    return out


def mine_term_patterns(md: str) -> list[dict[str, Any]]:
    """결정적 패턴 채굴 — 한영 병기·영문 약어·정의 문형(영/한)."""
    entries: list[dict[str, Any]] = []

    for m in _KO_PAREN_RE.finditer(md):
        inner = m.group("inner").strip()
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        if not 1 <= len(parts) <= 2:
            continue
        if any(not re.fullmatch(r"[A-Za-z0-9&/\-.'’ ]+", p) for p in parts):
            continue
        en: str | None = None
        abbr: str | None = None
        if len(parts) == 2:
            en, abbr = parts
            if not _looks_abbr(abbr):
                continue
        elif _looks_abbr(parts[0]):
            abbr = parts[0]
        else:
            en = parts[0]
        if abbr in _COMMON_ABBR:
            continue
        # 괄호 앞 문맥이 딸려 온 조각은 마지막 조각 이후만 용어로 남긴다.
        ko = m.group("ko")
        frags = list(_KO_FRAGMENT_RE.finditer(ko))
        if frags:
            ko = ko[frags[-1].end() :].strip()
        if not ko:
            continue
        entries.append(_entry(ko=ko, en=en, abbr=abbr))

    for m in _EN_ABBR_RE.finditer(md):
        abbr = m.group("abbr")
        if abbr in _COMMON_ABBR or not _looks_abbr(abbr):
            continue
        entries.append(_entry(en=m.group("en"), abbr=abbr))

    for pattern in (_QUOTED_DEF_RE, _IS_DEFINED_RE, _EQUIV_RE):
        for m in pattern.finditer(md):
            term = _trim_term_tail(" ".join(m.group("term").split()))
            if len(term) < 3:
                continue
            entries.append(_entry(en=term, definition=_sentence_around(md, m.start(), m.end())))

    for m in _KO_DEF_RE.finditer(md):
        term = m.group("term").strip()
        tokens = term.split()
        # 마지막 어절이 1자면 조사·지시어가 딸려 온 것("미국과 이"란)이다 — 버린다.
        if not tokens or len(tokens[-1]) < 2:
            continue
        entries.append(_entry(ko=term, definition=_sentence_around(md, m.start(), m.end("body"))))

    return merge_entries(entries)


# ── LLM 보강 ────────────────────────────────────────────────────────────────

_LLM_SYSTEM = (
    "너는 문서 용어 색인 담당자다. 주어진 문서에서 두 가지만 추출한다.\n"
    "1) 문서가 **스스로 정의하는 용어**: 'X means …', 'X is defined as …', "
    "'X란 …을 말한다'처럼 문서 안에 정의 문장이 실제로 있는 것만. definition에는 "
    "그 정의 문장을 요약하지 말고 원문 그대로(240자 이내) 옮긴다.\n"
    "2) 한↔영 대응 표기: 본문에 병기된 것만"
    "(예: 에너지속성인증서(Energy Attribute Certificate, EAC)).\n"
    "문서에 없는 정의를 상식으로 만들어 넣지 마라. 확신이 없으면 뺀다. "
    "항목이 없으면 빈 배열을 낸다. 마지막에 JSON만 출력한다:\n"
    '```json\n{"terms": [{"ko": "", "en": "", "abbr": "", "definition": ""}]}\n```'
)


def _llm_input(md: str) -> str:
    if len(md) <= _LLM_INPUT_HEAD + _LLM_INPUT_TAIL:
        return md
    return md[:_LLM_INPUT_HEAD] + "\n…(중략)…\n" + md[-_LLM_INPUT_TAIL:]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _validate_llm_terms(raw: Any, source_text: str) -> list[dict[str, Any]]:
    """LLM 산출 검증 — 형태 검사 + definition의 본문 실재 검사(지어낸 정의 차단)."""
    if not isinstance(raw, list):
        return []
    haystack = _norm(source_text)
    out: list[dict[str, Any]] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        e = _entry(
            ko=str(item.get("ko") or ""),
            en=str(item.get("en") or ""),
            abbr=str(item.get("abbr") or ""),
            definition=str(item.get("definition") or ""),
            origin="llm",
        )
        if not (e["ko"] or e["en"] or e["abbr"]):
            continue
        if e["definition"]:
            probe = _norm(e["definition"])[:80].rstrip("…")
            if probe and probe not in haystack:
                e["definition"] = None
                dropped += 1
        out.append(e)
    if dropped:
        logger.info("term_mining.llm_definitions_dropped", n_dropped=dropped)
    return out


async def _mine_llm(
    md: str, *, project_id: UUID, client: Any = None, model: str | None = None
) -> list[dict[str, Any]]:
    from src.clients.llm.base import CompletionRequest, Message
    from src.clients.llm.factory import get_llm_client
    from src.clients.llm.token_tracker import token_context
    from src.services.generation.planner import _parse_manifest

    client = client or get_llm_client()
    body = _llm_input(md)
    request = CompletionRequest(
        messages=[Message(role="user", content=f"문서 본문:\n\n{body}")],
        model=model or settings.term_mining_model,
        system=_LLM_SYSTEM,
        temperature=0.0,
        max_tokens=_LLM_MAX_TOKENS,
    )
    with token_context(project_id=project_id, operation="indexing.term_mining"):
        response = await client.complete(request)
    manifest = _parse_manifest(response.content)
    raw = manifest.get("terms") if isinstance(manifest, dict) else None
    return _validate_llm_terms(raw, body)


async def mine_terms(
    md: str,
    *,
    project_id: UUID,
    use_llm: bool = True,
    client: Any = None,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """패턴 + (설정 시) LLM 보강 채굴. (entries, mining_meta)를 돌려준다.

    LLM 실패는 비치명 — 패턴 결과만으로 진행한다. 패턴이 결정적 정본이고 LLM은
    빈 칸을 채우는 쪽이다(merge_entries에서 패턴이 먼저).
    """
    pattern_terms = mine_term_patterns(md)
    llm_terms: list[dict[str, Any]] = []
    llm_ran = False
    if use_llm and settings.term_mining_llm and len(md) >= _LLM_MIN_INPUT:
        try:
            llm_terms = await _mine_llm(md, project_id=project_id, client=client, model=model)
            llm_ran = True
        except Exception:
            logger.warning("term_mining.llm_failed", project_id=str(project_id), exc_info=True)
    entries = merge_entries(pattern_terms, llm_terms)
    meta = {
        "llm": llm_ran,
        "n_pattern": len(pattern_terms),
        "n_llm": len(llm_terms),
        "n_total": len(entries),
    }
    return entries, meta


async def mine_and_store_quietly(
    session_maker: Any,
    source_id: UUID,
    md: str,
    *,
    project_id: UUID,
    use_llm: bool = True,
) -> int:
    """색인이 부르는 문 — 실패해도 색인을 막지 않는다(span_vectors.store_quietly 계약).

    같은 본문 재색인은 해시로 건너뛴다 — 재색인마다 LLM 콜이 다시 붙으면 안 된다.
    LLM 콜은 DB 세션 밖에서 한다(느린 외부 I/O가 트랜잭션을 물고 있으면 안 된다).
    """
    try:
        if not (md or "").strip():
            return 0
        digest = hashlib.md5(md.encode("utf-8")).hexdigest()
        async with session_maker() as session:
            row = await _get_source(session, source_id)
            if row is None:
                return 0
            meta = dict(row.metadata_ or {})
            prev = meta.get(TERM_MINING_KEY) or {}
            if prev.get("hash") == digest and TERM_ENTRIES_KEY in meta:
                return len(meta[TERM_ENTRIES_KEY])

        entries, mining = await mine_terms(md, project_id=project_id, use_llm=use_llm)
        mining["hash"] = digest

        async with session_maker() as session:
            row = await _get_source(session, source_id)
            if row is None:
                return 0
            meta = dict(row.metadata_ or {})
            meta[TERM_ENTRIES_KEY] = entries
            meta[TERM_MINING_KEY] = mining
            row.metadata_ = meta  # JSONB는 재할당해야 dirty로 잡힌다
            await session.commit()
        logger.info(
            "term_mining.stored",
            source_id=str(source_id),
            n_terms=len(entries),
            n_pattern=mining["n_pattern"],
            llm=mining["llm"],
        )
        return len(entries)
    except Exception:
        logger.warning("term_mining.failed", source_id=str(source_id), exc_info=True)
        return 0


async def _get_source(session: Any, source_id: UUID) -> Any:
    from src.db.models.project_source import ProjectSource

    return await session.get(ProjectSource, source_id)
