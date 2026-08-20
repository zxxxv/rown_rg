"""사실 대장(fact ledger) — 절이 확정한 값을 적어 두고, 뒤 절이 그대로 쓰게 하고,
어긋나면 잡아내는 장부. 설계 확정본 2026-08-15([[project_fact_ledger_design]]).

세 소비자가 한 장부를 본다(검출기·주입 이중 투자 금지 — 단일 원천 원칙):
1. **주입**: builds_on 절 작성 직전, 대상 절의 엔트리를 구조화된 값으로 프롬프트에
   싣는다. 서술 요약으로 잇는 실험은 무근거 +39% 순손해였다 — 값·단위·근거만 넘긴다.
2. **검출**: assemble에서 문서 전체를 취합해 "같은 지표 다른 값"을 조인으로 잡는다
   (3차 런 실측: CCA 탄소가격이 3.1·3.2=60달러 vs 3.4·3.5=55달러로 갈렸는데 어떤
   검사도 못 봤다 — 각 절이 자기 출처에 충실했기 때문. 절 간 대조만이 잡는 유형).
3. **화면**: PM 경고 + 엔트리 chunk_ids로 근거 드로어 직행.

절충(2026-08-15 확정): **전부 적립하되 주입은 table·explicit만, 검출은 prose까지**
— 오탐 비용이 비대칭이다(검출 오탐=경고 하나, 주입 오탐=틀린 라벨의 값이 '확정'
으로 생성 경로에 들어가 오염).

추출은 전부 결정적이다 — LLM 추출은 라벨 요동 실측으로 금지(consistency_graph 교훈).
저장 정본 = sections.meta["ledger_entries"](새 테이블 없음), 적립 시점 = 절 작성 완료
직후(write 루프). 인용 마커가 그 시점엔 **로컬 번호**(근거 풀 인덱스)이므로 여기서
chunk_id로 풀어 저장한다 — 번호는 assemble renumber에서 바뀌지만 청크 id는 불변이라
편집·재작성·근거 드로어까지 살아남는다.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.builds_on import parse_ref
from src.services.qa.gate import significant_numbers
from src.services.qa.table_check import _TABLE_LINE_RE, _cells_of, _clean_label

# 주입 캡 — 절 번호만 지정한 참조("4.1")가 받는 엔트리 상한. 장 전체("4.*")도 절당
# 이 캡을 적용해 합친다. 값이 열 개를 넘으면 그건 전달이 아니라 요약 대상이다.
INJECT_CAP_PER_SECTION = 10

# 인용 마커 (출처 n[, m…]) — 작성 시점 로컬 번호. [n] 직접 인용도 같은 번호 체계.
_MARKER_RE = re.compile(r"\((?:출처|근거)\s*([0-9,\s]+)\)|\[([0-9]+)\]")
# 개조식 명시 패턴: "지표명: 값 …" (불릿 머리 허용). 라벨은 짧아야 지표다 —
# 길면 문장이고, 문장은 prose 백스톱이 담당한다.
_EXPLICIT_RE = re.compile(r"^[\s\-ㅇ□o·•*]*\(?([가-힣A-Za-z0-9 ·/]{2,30})\)?\s*[:：]\s*(.+)$")
# 불릿 라벨 "(국내 참여 현황) 문장…" — prose 엔트리의 지표명 후보.
_BULLET_LABEL_RE = re.compile(r"^[\s\-ㅇ□o·•*]*\(([가-힣A-Za-z0-9 ·/]{2,24})\)\s*(.+)$")
# 값 토큰: 수치 + 단위 꼬리. 단위가 있어야 조인에서 배율 혼동을 막는다.
_VALUE_RE = re.compile(
    r"([0-9][0-9,.]*)\s*(조\s*원|억\s*원|만\s*원|원|%|%p|달러|유로|TWh|GWh|MWh|kWh|톤|건|개사?|명|배|년|월)?"
)
# 시점 한정자: "2024년", "'24년", "’24년" — 값의 기준 시점.
_YEAR_RE = re.compile(r"(?:20\d{2}|[’']\d{2})\s*년?")
# range 표기: "0.9~2.7조 원" — max를 point로 승격하는 패턴의 원천.
_RANGE_RE = re.compile(r"([0-9][0-9,.]*)\s*[~∼–-]\s*([0-9][0-9,.]*)")

# 값·한정자 추출 전에 걷어내는 잡음 — 4차 런 오탐 763건 해부의 직접 산물.
# 인용 마커의 번호가 값으로 적립된 게 611건("* 출처: …(출처 13)" → 출처=13),
# 규정·법안 번호가 값 행세한 게 그다음(모법=956 ← "규정(EU) 2023/956").
# 마커는 chunk_id 해소에 계속 쓰므로 원문에서 먼저 뽑고, 값 추출만 정제본을 본다.
_NOISE_RES = (
    _MARKER_RE,
    re.compile(r"(?:\((?:EU|EC)\)\s*)?(?:19|20)\d{2}\s*/\s*\d{1,5}"),  # 규정 2023/956
    re.compile(r"\d{1,5}\s*/\s*(?:19|20)\d{2}"),  # 구형 표기 1907/2006
    re.compile(r"\b[SH]\.\s?(?:R\.\s?)?\d{2,5}\b"),  # 미 의회 법안 S. 4355
    re.compile(r"제?\s*\d{2,3}\s*대\s*(?:의회|국회)"),  # 회기 "117대 의회"
    re.compile(r"제\s*\d+\s*호"),  # 법률 제18469호
)

# 지표가 될 수 없는 라벨 — 표기 장치(출처 줄·각주)와 표의 관용어. 정규화 완전일치 비교.
_METRIC_BLACKLIST = {
    "출처",
    "자료",
    "자료출처",
    "출전",
    "근거",
    "각주",
    "주석",
    "비고",
    "참고",
    "구분",
    "합계",
    "총계",
    "소계",
    "기타",
    "그림",
    "표",
}


def _strip_value_noise(text: str) -> str:
    for rx in _NOISE_RES:
        text = rx.sub(" ", text)
    return text


def _markers_in(text: str) -> list[int]:
    out: list[int] = []
    for m in _MARKER_RE.finditer(text):
        raw = m.group(1) or m.group(2) or ""
        for tok in raw.split(","):
            tok = tok.strip()
            if tok.isdigit():
                out.append(int(tok))
    return out


def _chunk_ids_for(markers: list[int], pool_chunk_ids: list[str]) -> list[str]:
    """로컬 마커 번호(1-base) → 청크 id. 범위 밖 번호는 조용히 버린다(환각 마커)."""
    out: list[str] = []
    for n in markers:
        if 1 <= n <= len(pool_chunk_ids):
            cid = pool_chunk_ids[n - 1]
            if cid not in out:
                out.append(cid)
    return out


def _qualifiers_of(text: str) -> dict[str, Any]:
    q: dict[str, Any] = {}
    year = _YEAR_RE.search(text)
    if year:
        q["시점"] = year.group(0).replace(" ", "")
    rng = _RANGE_RE.search(text)
    if rng:
        q["종류"] = "range"
        q["range"] = [rng.group(1).replace(",", ""), rng.group(2).replace(",", "")]
    return q


def _first_value(text: str) -> tuple[str, str | None] | None:
    """텍스트에서 유의미 수치 하나와 단위. 연도·용어 숫자는 significant_numbers가 거른다."""
    sig = set(significant_numbers(text))
    if not sig:
        return None
    for m in _VALUE_RE.finditer(text):
        norm = m.group(1).replace(",", "")
        if m.group(1) in sig or norm in sig:
            unit = (m.group(2) or "").replace(" ", "") or None
            return norm, unit
    return None


def _entry(
    *,
    metric: str,
    value: str,
    unit: str | None,
    qualifiers: dict[str, Any],
    section_ref: str,
    chunk_ids: list[str],
    source_kind: str,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "value": value,
        "unit": unit,
        "qualifiers": qualifiers,
        "section_ref": section_ref,
        "chunk_ids": chunk_ids,
        "source_kind": source_kind,
    }


def _table_blocks(content: str) -> list[tuple[list[list[str]], str]]:
    """표 블록들 → (행 셀들, 블록+직후 출처 줄 텍스트). 출처 줄은 표 바로 아래 '* 출처:'."""
    lines = content.split("\n")
    out: list[tuple[list[list[str]], str]] = []
    i = 0
    while i < len(lines):
        if not _TABLE_LINE_RE.match(lines[i]):
            i += 1
            continue
        start = i
        block: list[list[str]] = []
        while i < len(lines) and _TABLE_LINE_RE.match(lines[i]):
            block.append(_cells_of(lines[i]))
            i += 1
        tail = lines[i] if i < len(lines) and "출처" in lines[i] else ""
        if len(block) >= 2:
            out.append((block, "\n".join(lines[start:i]) + "\n" + tail))
    return out


def _is_rule_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells)


def extract_entries(
    content: str, section_ref: str, pool_chunk_ids: list[str]
) -> list[dict[str, Any]]:
    """절 본문 → 사실 대장 엔트리(결정적). 표 → 명시 → prose 순, 같은 값 중복 제거.

    prose는 검출 전용 백스톱이라 불릿 라벨이 있는 줄만 담는다 — 라벨 없는 문장에서
    지표명을 결정적으로 뽑을 방법이 없고, 약한 지표명은 조인 오탐만 만든다.
    """
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(e: dict[str, Any]) -> None:
        key = (e["metric"], e["value"])
        if not e["metric"] or not e["value"] or key in seen:
            return
        if _norm_metric(e["metric"]) in _METRIC_BLACKLIST:
            return
        seen.add(key)
        entries.append(e)

    # ── 표: 행 라벨 = 지표, 열 머리 = 한정자. 출처는 블록 전체 마커 ∪ 출처 줄.
    for block, block_text in _table_blocks(content):
        header = block[0]
        chunk_ids = _chunk_ids_for(_markers_in(block_text), pool_chunk_ids)
        for row in block[1:]:
            if _is_rule_row(row) or not row:
                continue
            row_label = _clean_label(row[0])
            if not row_label:
                continue
            for ci, cell in enumerate(row[1:], start=1):
                cell_clean = _strip_value_noise(cell)
                found = _first_value(cell_clean)
                if found is None:
                    continue
                value, unit = found
                q = _qualifiers_of(cell_clean)
                col = _clean_label(header[ci]) if ci < len(header) else ""
                if col:
                    q["열"] = col
                    if not q.get("시점"):
                        y = _YEAR_RE.search(header[ci])
                        if y:
                            q["시점"] = y.group(0).replace(" ", "")
                add(
                    _entry(
                        metric=row_label,
                        value=value,
                        unit=unit,
                        qualifiers=q,
                        section_ref=section_ref,
                        chunk_ids=chunk_ids,
                        source_kind="table",
                    )
                )

    # ── 명시·prose: 표 줄은 건너뛴다(위에서 처리).
    for line in content.split("\n"):
        if _TABLE_LINE_RE.match(line) or line.lstrip().startswith("#"):
            continue
        chunk_ids = _chunk_ids_for(_markers_in(line), pool_chunk_ids)
        m = _EXPLICIT_RE.match(line)
        if m:
            metric = _clean_label(m.group(1))
            tail = _strip_value_noise(m.group(2))
            found = _first_value(tail)
            if metric and found:
                value, unit = found
                add(
                    _entry(
                        metric=metric,
                        value=value,
                        unit=unit,
                        qualifiers=_qualifiers_of(tail),
                        section_ref=section_ref,
                        chunk_ids=chunk_ids,
                        source_kind="explicit",
                    )
                )
                continue
        b = _BULLET_LABEL_RE.match(line)
        if b:
            btail = _strip_value_noise(b.group(2))
            found = _first_value(btail)
            if found:
                value, unit = found
                add(
                    _entry(
                        metric=_clean_label(b.group(1)),
                        value=value,
                        unit=unit,
                        qualifiers=_qualifiers_of(btail),
                        section_ref=section_ref,
                        chunk_ids=chunk_ids,
                        source_kind="prose",
                    )
                )
    return entries


# ─── 주입 ────────────────────────────────────────────────────────────────────


def injectable(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """주입 대상 엔트리 — table·explicit만(절충 확정), 절당 캡."""
    picked = [e for e in entries if e.get("source_kind") in ("table", "explicit")]
    return picked[:INJECT_CAP_PER_SECTION]


def select_for_refs(
    refs: list[str],
    ledger_by_section: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """builds_on 표기 목록 → 주입 엔트리 + 경고.

    - "4.1"        → 그 절의 table/explicit 전체(캡)
    - "4.1(지표)"  → 지표명 매칭(정규화 부분일치) — 못 찾으면 경고 + 그 절 전체로 완화
    - "4.*"        → 장 전체(절마다 캡 적용 후 합침)
    """
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_keys: set[tuple[str, str, str]] = set()

    def push(items: list[dict[str, Any]]) -> None:
        for e in items:
            key = (e["section_ref"], e["metric"], str(e["value"]))
            if key not in seen_keys:
                seen_keys.add(key)
                out.append(e)

    for raw in refs:
        ref = parse_ref(raw)
        if ref is None:
            continue
        if ref.section is None:
            chapter_secs = [s for s in ledger_by_section if s.split(".")[0] == str(ref.chapter)]
            for sec in sorted(chapter_secs):
                push(injectable(ledger_by_section.get(sec, [])))
            if not chapter_secs:
                warnings.append(f"{raw}: 장에 적립된 확정값이 없음")
            continue
        base = f"{ref.chapter}.{ref.section}"
        pool = injectable(ledger_by_section.get(base, []))
        if not pool:
            warnings.append(f"{raw}: 대상 절에 적립된 확정값이 없음")
            continue
        if ref.metric:
            want = _norm_metric(ref.metric)
            hit = [
                e
                for e in pool
                if want in _norm_metric(e["metric"]) or _norm_metric(e["metric"]) in want
            ]
            if hit:
                push(hit)
            else:
                warnings.append(f"{raw}: 지표를 못 찾아 절 전체로 완화")
                push(pool)
        else:
            push(pool)
    return out, warnings


def format_injection(entries: list[dict[str, Any]], local_no_by_chunk: dict[str, int]) -> str:
    """주입 guidance 블록 — 서술문 금지, 구조화 값만.

    local_no_by_chunk: 청크 id → 이 절 근거 풀에서의 로컬 번호. 값의 원 청크가 풀에
    덧붙여져 있어야 작성기가 (출처 n)으로 인용하고, 그 마커가 renumber를 거쳐 원
    근거로 해소된다(인용 사슬 무결 — 판정축 ③).
    """
    if not entries:
        return ""
    lines = [
        "앞 절에서 확정된 값 — 아래 값을 그대로 사용하라. 다른 자료에서 같은 지표를"
        " 다시 찾거나 재계산하지 마라. 값을 쓸 때는 표기된 근거 번호를 인용하라."
        " 단, **이 절의 주제와 무관한 값은 쓰지 마라** — 목록에 있다고 전부 실을"
        " 필요 없다(v5c-2 실측: 시사점 절에 무관한 타 제도 세수가 유입됨):"
    ]
    for e in entries:
        unit = e.get("unit") or ""
        q = e.get("qualifiers") or {}
        tail = f"({q['시점']})" if q.get("시점") else ""
        nos = sorted(
            {local_no_by_chunk[c] for c in e.get("chunk_ids", []) if c in local_no_by_chunk}
        )
        cite = f" (출처 {', '.join(str(n) for n in nos)})" if nos else ""
        lines.append(f"- [{e['section_ref']}] {e['metric']}: {e['value']}{unit}{tail}{cite}")
    return "\n".join(lines)


# ─── 검출(조인) ──────────────────────────────────────────────────────────────


def _norm_metric(text: str) -> str:
    return re.sub(r"[\s·()\-]", "", str(text)).lower()


def _same_metric(a: str, b: str) -> bool:
    """지표명 동일성 — 정규화 완전일치.

    v1은 포함일치(3자+)였는데 4차 런 해부에서 '인지'⊆'인지도'류 과결합이 비-마커
    오탐의 한 축으로 실측돼 완전일치로 조였다. 참조 지정("6.2(총사업비)")의 매칭은
    사용자가 쓴 지표명이라 select_for_refs 쪽 부분일치를 유지한다. 임베딩 클러스터링은
    지표명 표본으로 임계를 새로 잰 뒤에 붙인다(문장 중복 0.9는 전이 불가).
    """
    na, nb = _norm_metric(a), _norm_metric(b)
    return bool(na) and na == nb


def _values_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if (a.get("unit") or "") != (b.get("unit") or ""):
        return False  # 단위가 다르면 다른 표현일 뿐 모순 단정 불가(억 원 vs 조 원 환산 등)
    try:
        va, vb = float(a["value"]), float(b["value"])
    except (TypeError, ValueError):
        return str(a["value"]) != str(b["value"])  # 범주값 — 문자열 불일치가 곧 충돌
    if va == vb:
        return False
    hi, lo = max(va, vb), min(va, vb)
    return lo > 0 and (hi - lo) / hi > 0.02  # 반올림·절사 허용(2%)


def _qualifiers_confirmed(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """critical 승격 조건 — 같은 것을 말했다는 **적극적** 확인이 있을 때만.

    v1은 '불일치만 아니면 일치'여서 한정자가 양쪽 다 빈 쌍이 전부 critical로
    승격됐다(4차 해부: 다른 설문 항목의 '인지', 다른 표의 행 라벨 '철강'류가
    비-마커 오탐 152건의 몸통). 설계 확정본 원문이 "미상 → 확인 필요"다.
    시점이 양쪽에 있고 같아야 confirmed, 표 열 머리·시나리오는 있으면 같아야 한다.
    """
    qa, qb = a.get("qualifiers") or {}, b.get("qualifiers") or {}
    if "시나리오" in qa and "시나리오" in qb and qa["시나리오"] != qb["시나리오"]:
        return False
    return "시점" in qa and "시점" in qb and qa["시점"] == qb["시점"]


def _different_table_column(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """열 머리가 양쪽에 있고 서로 다르면 아예 다른 지표다 — 경고도 내지 않는다.

    4차 해부: '철강'이 3.2 표(수출액)와 3.4 표(비용 증가율)에서 행 라벨로 겹쳐
    수십 건이 걸렸다. 행 라벨은 대상이고 지표의 정체는 열 머리에 있다.
    """
    ca = (a.get("qualifiers") or {}).get("열")
    cb = (b.get("qualifiers") or {}).get("열")
    return bool(ca) and bool(cb) and ca != cb


def _range_promotion(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """range 엔트리의 max를 다른 절이 point로 승격한 패턴(실측 4개 장 공통)."""
    for rng, point in ((a, b), (b, a)):
        r = (rng.get("qualifiers") or {}).get("range")
        if not r or (point.get("qualifiers") or {}).get("range"):
            continue
        try:
            if abs(float(point["value"]) - float(r[1])) <= 1e-9:
                return {"range_ref": rng["section_ref"], "point_ref": point["section_ref"]}
        except (TypeError, ValueError):
            continue
    return None


def join_conflicts(all_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """문서 취합 조인 — 같은 지표, 다른 절, 다른 값.

    티어(오탐 비용 비대칭 설계):
    - critical: 지표 일치 + 시점 한정자 **적극 일치**(양쪽에 있고 같음) + 값 상이
      — "같은 시점의 같은 것을 다르게 말했다"
    - warning(확인 필요): 한정자 불일치·미상 — 1.8조=기준 vs 2.7조=상단일 수 있고,
      한정자 없는 쌍은 다른 설문 항목·다른 표의 같은 행 라벨일 수 있다(4차 실측)
    - warning(범위 승격): range의 max가 딴 절에서 point로 굳음
    """
    findings: list[dict[str, Any]] = []
    reported: set[tuple[str, str, str, str]] = set()
    # 한 절 안에서 여러 값을 갖는 지표명은 식별자가 아니라 목록 범주다("정책과제:
    # 29.2%·16.4%·…" 같은 설문 보기 나열 — 4차 해부에서 짝조합 20건 폭발). 조인 제외.
    values_by_key: dict[tuple[str, str], set[str]] = {}
    for e in all_entries:
        values_by_key.setdefault((_norm_metric(e["metric"]), e["section_ref"]), set()).add(
            str(e["value"])
        )
    ambiguous = {k for k, vals in values_by_key.items() if len(vals) > 1}
    for i, a in enumerate(all_entries):
        for b in all_entries[i + 1 :]:
            if a["section_ref"] == b["section_ref"]:
                continue
            if (_norm_metric(a["metric"]), a["section_ref"]) in ambiguous or (
                _norm_metric(b["metric"]),
                b["section_ref"],
            ) in ambiguous:
                continue
            if not _same_metric(a["metric"], b["metric"]):
                continue
            if _different_table_column(a, b):
                continue
            promo = _range_promotion(a, b)
            if promo:
                key = (a["section_ref"], b["section_ref"], a["metric"], "promo")
                if key not in reported:
                    reported.add(key)
                    findings.append(
                        {
                            "severity": "warning",
                            "category": "절 간 지표 충돌",
                            "section_ref": promo["point_ref"],
                            "detail": (
                                f"범위의 상단이 확정값으로 굳음: {a['metric']} — "
                                f"{promo['range_ref']}는 범위, {promo['point_ref']}는 "
                                f"그 최댓값을 단일값으로 서술"
                            ),
                            "chunk_ids": list({*a.get("chunk_ids", []), *b.get("chunk_ids", [])}),
                        }
                    )
                continue
            if not _values_conflict(a, b):
                continue
            confirmed = _qualifiers_confirmed(a, b)
            key = (
                min(a["section_ref"], b["section_ref"]),
                max(a["section_ref"], b["section_ref"]),
                _norm_metric(a["metric"]),
                str(sorted([str(a["value"]), str(b["value"])])),
            )
            if key in reported:
                continue
            reported.add(key)
            unit = a.get("unit") or ""
            findings.append(
                {
                    "severity": "critical" if confirmed else "warning",
                    "category": "절 간 지표 충돌",
                    "section_ref": b["section_ref"],
                    "detail": (
                        f"같은 지표 다른 값: {a['metric']} — "
                        f"{a['section_ref']}={a['value']}{unit} vs "
                        f"{b['section_ref']}={b['value']}{unit}"
                        + ("" if confirmed else " (한정자 불일치·미상 — 확인 필요)")
                    ),
                    "chunk_ids": list({*a.get("chunk_ids", []), *b.get("chunk_ids", [])}),
                }
            )
    return findings
