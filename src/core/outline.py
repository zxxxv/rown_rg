"""outline 안정 식별자 — 절 정체성의 닻 (2026-08-21 설계).

절 정체성이 세 갈래로 갈려 있었다: section_id(UUID)는 목차를 한 글자만 고쳐도
전 절이 재발급됐고(plan 전량 폐기 → plan_from_outline이 uuid4 재발급), "4.1" 번호
문자열(builds_on·사실 대장·verify)은 절 삽입마다 뒤 절 전부가 말없이 다른 절을
가리켰으며, 절 제목 문자열(matched_sections)은 제목 편집에 끊겼다.

닻은 config.outline이다: 장·절 항목이 안정 id(uuid 문자열)를 지니고,
``plan_from_outline``이 그 id를 SectionPlan.section_id로 채택한다. 그러면 plan을
언제 재생성해도 같은 절은 같은 id를 유지하고, 번호(ch.sec)는 표시값으로 내려간다.

builds_on 저장 계약(2026-08-21 확정): **저작은 번호, 저장은 id 토큰.**
- 사람·프리셋은 종전대로 "4.1" | "4.1(총사업비)" | "4.*"를 쓴다(설계 확정본
  2026-08-15의 UX 계약 유지).
- 서버가 저장 시점에 제출된 목차의 위치로 대상을 해석해 id 토큰으로 정규화한다:
  "s:<uuid>" | "s:<uuid>(총사업비)" | "c:<uuid>"(장 전체).
- plan 재생성 시 토큰을 **현재** 번호 라벨로 되돌려 SectionPlan.builds_on에 싣는다 —
  하류(core/builds_on·services/ledger·write_loop)는 종전 라벨 계약 그대로.

이 모듈은 순수 부품이다(DB·LLM 무관): id 채움, 번호↔토큰 변환, 정규화 검증.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from src.core.builds_on import MAX_REFS_PER_SECTION, parse_ref

# 페이지 환산 계수(자/페이지) — 카탈로그 표기(15000~22500자 = 10~15p 등)의 기준.
# writer_context(분량 지시문)·prompts/personal(개인 규칙 표기)·design_brief(비용
# 추정)가 각자 1500을 재선언하다 통일했다(2026-09-03 감사 — 한 곳만 바꾸면 지시문과
# 화면 표기가 조용히 어긋나는 구조였다).
CHARS_PER_PAGE = 1500


def section_label_key(label: str) -> tuple[int, int]:
    """절 라벨("4.1")의 목차 정렬 키 — 문자열 정렬은 "10.1"을 "2.1" 앞에 둔다.

    리허설·중복 재작성·절 간 대조가 같은 함수를 세 벌 갖고 있었다(2026-09-03 통일).
    숫자가 아니면 (0, 0) — 맨 앞으로 보내되 예외는 안 낸다(옛 라벨 방어).
    """
    a, _, b = label.partition(".")
    try:
        return (int(a), int(b or 0))
    except ValueError:
        return (0, 0)


# 절당 에이전트 배정 상한 — 초과는 저장 단계에서 오류로 알린다(조용한 절단 금지).
# 프론트 project-config/validation.ts·프리셋 스키마(api/schemas/project)와 거울.
# 5인 이유(2026-08-28 사용자 결정): 절 분량 목표가 최대 2만자대로 고정이라 관점이
# 많을수록 관점당 지면이 얕아져 내용이 뭉개진다. 실측도 같은 방향 — 7명 절(철강
# 4.3)은 분량 목표는 채웠지만 공급 근거 75개 중 상당수가 잉여였고 비용만 +50~70%.
# 권장선(2~3)은 프론트 경고(AGENTS_WARN_THRESHOLD)가 맡는다.
MAX_AGENTS_PER_SECTION = 5

# 작성 방향의 열거 축 — "사회·기술·경제·환경·정치 분석"처럼 가운뎃점으로 3개 이상
# 잇는 열거는 그 절이 커버해야 할 축의 선언이다. 2026-08-29 철강 실사고: 이 열거가
# 검색 질의 어디에도 안 실려(방향은 작성 프롬프트로만 감) 사회·경제·정치 소재가
# 수집 자체가 안 됐고, 작성기는 소재 없는 축을 조용히 생략했다.
# 소비처: retrieval.section(축별 질의 승격)·generation.brief_ai(질의 상한 확장).
_AXIS_ENUM_RE = re.compile(r"[가-힣A-Za-z0-9]{1,14}(?:\s*[·ㆍ・]\s*[가-힣A-Za-z0-9]{1,14}){2,}")
MAX_DIRECTION_AXES = 6


def direction_axes(direction: str) -> list[str]:
    """작성 방향에 열거된 축들 — 가장 긴 열거 하나를 골라 쪼갠다(상한 6).

    열거가 여럿이면 긴 쪽이 절의 뼈대일 확률이 높다. 2개짜리는 대구("국내·외")라
    축 선언으로 안 본다.
    """
    runs = _AXIS_ENUM_RE.findall(" ".join((direction or "").split()))
    if not runs:
        return []
    best = max(runs, key=lambda r: len(re.split(r"[·ㆍ・]", r)))
    items = [t.strip() for t in re.split(r"[·ㆍ・]", best) if t.strip()]
    return items[:MAX_DIRECTION_AXES] if len(items) >= 3 else []


# id 토큰 표기 — 사람 입력("4.1")과 정규식이 겹칠 수 없게 접두를 붙인다.
_SECTION_TOKEN_RE = re.compile(
    r"""^\s*s:(?P<id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})
    (?:\(\s*(?P<metric>[^()]+?)\s*\))?\s*$""",
    re.VERBOSE,
)
_CHAPTER_TOKEN_RE = re.compile(
    r"""^\s*c:(?P<id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})
    (?:\.\*)?\s*$""",
    re.VERBOSE,
)


def format_section_token(section_id: str, metric: str | None = None) -> str:
    return f"s:{section_id}({metric})" if metric else f"s:{section_id}"


def format_chapter_token(chapter_id: str) -> str:
    return f"c:{chapter_id}"


def parse_id_token(raw: str) -> tuple[str, str, str | None] | None:
    """id 토큰 해석 → (종류 "section"|"chapter", id 소문자, 지표|None). 아니면 None."""
    m = _SECTION_TOKEN_RE.match(raw or "")
    if m is not None:
        metric = (m.group("metric") or "").strip() or None
        return ("section", m.group("id").lower(), metric)
    m = _CHAPTER_TOKEN_RE.match(raw or "")
    if m is not None:
        return ("chapter", m.group("id").lower(), None)
    return None


def _valid_id(raw: Any) -> str | None:
    """항목 id로 쓸 수 있으면 정규화된 uuid 문자열, 아니면 None."""
    try:
        return str(UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        return None


def ensure_outline_ids(
    outline: dict[str, Any], *, fresh: bool = False
) -> tuple[dict[str, Any], dict[str, str]]:
    """장·절에 안정 id를 채운 **새** outline dict과 (옛 id → 새 id) 재매핑.

    있는 id는 보존한다(정체성 유지가 목적이므로 함부로 재발급하지 않는다).
    없거나 uuid가 아니거나 중복이면 그 항목만 새로 발급한다 — 중복은 장 복사
    버그·수동 편집에서 올 수 있고, 남겨 두면 plan의 PK가 충돌한다.

    fresh=True면 전 항목을 새로 발급한다 — 프로젝트 **생성** 전용. sections.id는
    전역 PK라, 남의 프로젝트 config를 복사해 만들어도 id가 겹치면 안 된다. 재매핑은
    builds_on 토큰이 옛 id를 가리킬 때 새 id로 옮겨 다는 데 쓴다.
    """
    seen: set[str] = set()
    remap: dict[str, str] = {}

    def _claim(raw: Any) -> str:
        old = _valid_id(raw)
        cid = old
        if fresh or cid is None or cid in seen:
            cid = str(uuid4())
            if old is not None and old not in remap:
                remap[old] = cid
        seen.add(cid)
        return cid

    chapters_out: list[dict[str, Any]] = []
    for chapter in outline.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        ch = dict(chapter)
        ch["id"] = _claim(ch.get("id"))
        sections_out: list[dict[str, Any]] = []
        for sec in ch.get("sections") or []:
            if not isinstance(sec, dict):
                continue
            s = dict(sec)
            s["id"] = _claim(s.get("id"))
            sections_out.append(s)
        ch["sections"] = sections_out
        chapters_out.append(ch)
    return {**outline, "chapters": chapters_out}, remap


def position_maps(
    outline: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[tuple[int, int], str], dict[int, str]]:
    """(절id→"4.1" 라벨, 장id→"4", 위치→절id, 장번호→장id). 번호는 배열 위치 파생."""
    label_by_sec: dict[str, str] = {}
    num_by_ch: dict[str, str] = {}
    sec_by_pos: dict[tuple[int, int], str] = {}
    ch_by_num: dict[int, str] = {}
    for ci, chapter in enumerate(outline.get("chapters") or [], start=1):
        if not isinstance(chapter, dict):
            continue
        ch_id = _valid_id(chapter.get("id"))
        if ch_id is not None:
            num_by_ch[ch_id] = str(ci)
            ch_by_num[ci] = ch_id
        for si, sec in enumerate(chapter.get("sections") or [], start=1):
            if not isinstance(sec, dict):
                continue
            sec_id = _valid_id(sec.get("id"))
            if sec_id is None:
                continue
            label_by_sec[sec_id] = f"{ci}.{si}"
            sec_by_pos[(ci, si)] = sec_id
    return label_by_sec, num_by_ch, sec_by_pos, ch_by_num


def normalize_outline(
    outline: dict[str, Any], *, fresh_ids: bool = False
) -> tuple[dict[str, Any], list[str]]:
    """id 채움 + builds_on을 id 토큰으로 정규화한 새 outline과 오류 목록.

    받는 표기: 번호("4.1"|"4.1(지표)"|"4.*" — 제출된 목차의 **현재 위치** 기준으로
    해석) 또는 이미 정규화된 id 토큰(재제출 round-trip). 오류(못 읽는 표기·유령
    대상·자기 참조·상한 초과)는 문구 목록으로 돌려준다 — 생성·수정은 사람이 고칠
    수 있는 마지막 자리라 조용한 절단 대신 명시적으로 알린다(기존 검증과 동일).

    fresh_ids=True(생성 전용)면 전 항목 id를 새로 발급하고, 옛 id를 가리키던
    토큰은 재매핑을 따라 새 id로 옮겨 단다.
    """
    normalized, remap = ensure_outline_ids(outline, fresh=fresh_ids)
    label_by_sec, num_by_ch, sec_by_pos, ch_by_num = position_maps(normalized)
    errors: list[str] = []

    for chapter in normalized["chapters"]:
        for sec in chapter["sections"]:
            # 에이전트 과다 배정 가드 — 에이전트마다 검색 질의가 따로 돌아 절 비용과
            # 근거팩이 배로 늘고, 사용자는 그 비용 구조를 모른 채 막 배정할 수 있다
            # (2026-08-28 지시). 프론트 validation.ts가 같은 상한을 미리 보여준다.
            agents = sec.get("analysts")
            if isinstance(agents, list) and len(agents) > MAX_AGENTS_PER_SECTION:
                label = label_by_sec.get(sec.get("id", ""), "?")
                errors.append(
                    f"{label}절 담당 에이전트({len(agents)}명)가 절당 상한"
                    f"({MAX_AGENTS_PER_SECTION}명)을 넘습니다"
                )
            raw_refs = sec.get("builds_on")
            if not raw_refs:
                continue
            out: list[str] = []
            seen: set[str] = set()
            self_id = sec["id"]
            label = label_by_sec.get(self_id, "?")
            for raw in raw_refs:
                token = _normalize_ref(
                    str(raw),
                    self_id=self_id,
                    label_by_sec=label_by_sec,
                    num_by_ch=num_by_ch,
                    sec_by_pos=sec_by_pos,
                    ch_by_num=ch_by_num,
                    errors=errors,
                    self_label=label,
                    remap=remap,
                )
                if token is None or token in seen:
                    continue
                if len(out) >= MAX_REFS_PER_SECTION:
                    errors.append(
                        f"{label}절 builds_on이 절당 상한({MAX_REFS_PER_SECTION})을 넘습니다"
                    )
                    break
                seen.add(token)
                out.append(token)
            sec["builds_on"] = out
    return normalized, errors


def _normalize_ref(
    raw: str,
    *,
    self_id: str,
    label_by_sec: dict[str, str],
    num_by_ch: dict[str, str],
    sec_by_pos: dict[tuple[int, int], str],
    ch_by_num: dict[int, str],
    errors: list[str],
    self_label: str,
    remap: dict[str, str] | None = None,
) -> str | None:
    """표기 1개 → 정규화된 id 토큰. 오류는 errors에 적고 None."""
    parsed_token = parse_id_token(raw)
    if parsed_token is not None:
        kind, tid, metric = parsed_token
        if remap:
            tid = remap.get(tid, tid)
        if kind == "chapter":
            if tid not in num_by_ch:
                errors.append(f"{self_label}절 builds_on이 없는 장을 참조합니다: {raw.strip()}")
                return None
            return format_chapter_token(tid)
        if tid not in label_by_sec:
            errors.append(f"{self_label}절 builds_on이 없는 절을 참조합니다: {raw.strip()}")
            return None
        if tid == self_id:
            errors.append(f"{self_label}절 builds_on이 자기 자신을 참조합니다")
            return None
        return format_section_token(tid, metric)
    ref = parse_ref(raw)
    if ref is None:
        errors.append(f"{self_label}절 builds_on 표기를 읽을 수 없습니다: {raw.strip()!r}")
        return None
    if ref.section is None:
        ch_id = ch_by_num.get(ref.chapter)
        if ch_id is None:
            errors.append(f"{self_label}절 builds_on이 없는 장을 참조합니다: {ref.label}")
            return None
        return format_chapter_token(ch_id)
    target = sec_by_pos.get((ref.chapter, ref.section))
    if target is None:
        errors.append(f"{self_label}절 builds_on이 없는 절을 참조합니다: {ref.label}")
        return None
    if target == self_id:
        errors.append(f"{self_label}절 builds_on이 자기 자신을 참조합니다")
        return None
    return format_section_token(target, ref.metric)


def token_to_label(raw: str, label_by_sec: dict[str, str], num_by_ch: dict[str, str]) -> str | None:
    """id 토큰 → 현재 번호 라벨("4.1"|"4.1(지표)"|"4.*").

    번호 문자열은 그대로 통과시킨다(마이그레이션 전 config·프리셋 잔재 —
    위치 해석이라는 옛 의미 그대로 하류에 넘긴다). 해석 불가 토큰은 None
    (대상 절이 삭제된 경우 — 의존만 사라지고 실행은 계속돼야 한다).
    """
    parsed = parse_id_token(raw)
    if parsed is None:
        return raw if parse_ref(raw) is not None else None
    kind, tid, metric = parsed
    if kind == "chapter":
        num = num_by_ch.get(tid)
        return f"{num}.*" if num is not None else None
    label = label_by_sec.get(tid)
    if label is None:
        return None
    return f"{label}({metric})" if metric else label
