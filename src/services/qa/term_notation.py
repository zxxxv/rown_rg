"""용어 병기 대조 — 본문의 "한글(원어)" 쌍을 용어표·보고서 자신과 결정적으로 대조.

용어 주입(generation/term_rules)이 예방이라면 이것은 탐지다(2026-08-27 사용자 결정).
원어 병기 관행 덕에 모델이 번역을 어떻게 확정했는지가 본문에 증거로 남는다 — 그 쌍을
전부 긁어 세 가지를 본다. 전부 문자열 대조라 LLM 콜 0, 재실행해도 같은 답이다.

1. 용어 표기 불일치(warning): 용어표(색인이 캔 한영 대응)에 한글 표기가 있는데 본문이
   다르게 옮긴 경우 — "재생에너지 인증서(EAC)" vs 용어표 "에너지속성인증서".
2. 용어 표기 요동(warning): 같은 원어를 보고서 안에서 두 가지 이상의 한글로 쓴 경우 —
   1장 "에너지속성인증서", 3장 "에너지 속성 인증서". 용어표 없이도 성립한다.
3. 정의 용어 표기 검토(warning, 보고서당 1행): 자료가 스스로 정의한 용어(operational
   commencement류)가 본문에 병기된 목록 — 한글 뜻 대조는 기계로 판정할 수 없으므로
   사람이 훑을 짧은 목록으로만 만든다. v6의 "사업 개시" 오역이 이 목록에서 잡힌다.

병기 추출 문법은 색인 채굴(indexing/terms)의 것을 그대로 쓴다 — 두 쪽이 다른 규칙을
쓰면 채굴은 됐는데 대조는 안 되는(또는 그 반대) 어긋남이 생긴다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from src.services.indexing.terms import (
    _KO_FRAGMENT_RE,
    _KO_PAREN_RE,
    _looks_abbr,
)

# 경고 하나에 싣는 예시 상한 — 목록 나열은 화면 몫이다(evidence_findings와 같은 철학).
_MAX_SAMPLES = 5
_MAX_REVIEW_TERMS = 8
# 요동 경고 상한 — v6 실측 50행은 신호 침수다. 변형 수가 많은 순으로 남기고 나머지는
# 요약 한 행으로 접는다.
_MAX_VARIANCE_ROWS = 12
# 접미 병합에서 짧은 쪽이 이보다 짧으면 흡수하지 않는다 — "인증서"(3자)가 모든
# ○○인증서를 삼켜 진짜 구분(녹색전력/녹색에너지)까지 뭉개진다.
_MIN_ABSORB_CHARS = 4


def _collapse_variants(kos: dict[str, list[str]]) -> dict[str, list[str]]:
    """표기 변형 병합 — 띄어쓰기 차이와 문맥 접미 관계는 같은 표기로 취급한다.

    v6 실측: "분리되어 기술자문그룹" vs "기술자문그룹"은 표기 요동이 아니라 앞말이
    딸려 온 것이고, "탄소 누출" vs "탄소누출"은 조판 손질 영역이다. 공백을 걷어낸
    문자열이 같거나 한쪽이 다른 쪽의 접미면(짧은 쪽 ≥ 4자) 짧은 표기로 합친다.
    "녹색전력인증서" vs "녹색에너지인증서"처럼 접미 관계가 아닌 것만 요동으로 남는다.
    """
    reps: list[tuple[str, str, list[str]]] = []  # (공백 제거형, 대표 표기, 절들)
    for ko in sorted(kos, key=lambda k: len(k.replace(" ", ""))):
        bare = ko.replace(" ", "")
        refs = kos[ko]
        merged = False
        for i, (rep_bare, rep_ko, rep_refs) in enumerate(reps):
            if bare == rep_bare or (len(rep_bare) >= _MIN_ABSORB_CHARS and bare.endswith(rep_bare)):
                reps[i] = (rep_bare, rep_ko, rep_refs + [r for r in refs if r not in rep_refs])
                merged = True
                break
        if not merged:
            reps.append((bare, ko, list(refs)))
    return {ko: refs for _, ko, refs in reps}


def _finding(
    chapter: int, section_ref: str, severity: str, category: str, detail: str
) -> dict[str, Any]:
    return {
        "chapter_number": chapter,
        "severity": severity,
        "category": category,
        "section_ref": section_ref,
        "detail": detail,
    }


def _norm_en(text: str) -> str:
    return " ".join(text.split()).lower()


def extract_pairs(content: str) -> list[tuple[str, str, str | None]]:
    """본문에서 (한글, 원어, 약어|None) 병기 쌍을 뽑는다 — 채굴기와 같은 문법.

    (출처 n)·(단위: %) 같은 괄호는 원어가 영문으로 시작하지 않아 걸리지 않는다.
    """
    out: list[tuple[str, str, str | None]] = []
    for m in _KO_PAREN_RE.finditer(content):
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
        ko = m.group("ko")
        frags = list(_KO_FRAGMENT_RE.finditer(ko))
        if frags:
            ko = ko[frags[-1].end() :].strip()
        if not ko:
            continue
        out.append((ko, en or abbr or "", abbr))
    return [(ko, orig, abbr) for ko, orig, abbr in out if orig]


def term_notation_findings(
    sections: list[tuple[int, str, str]],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """(장 번호, 절 참조, 본문) 목록 + 용어표 → 병기 대조 경고 행들.

    entries는 generation/term_rules.load_project_terms의 형태(자료 간 병합 없음,
    source_title 동봉)를 그대로 받는다. 비어 있어도 요동 검사는 성립한다.
    """
    # 본문 쌍 적립: 원어 키 → {한글 표기 → [절 참조들]}, 첫 등장 위치도 기억한다.
    seen: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    first_at: dict[str, tuple[int, str]] = {}
    for chapter, ref, content in sections:
        for ko, orig, _abbr in extract_pairs(content or ""):
            key = _norm_en(orig)
            bucket = seen[key]
            if ref not in bucket[ko]:
                bucket[ko].append(ref)
            first_at.setdefault(key, (chapter, ref))

    out: list[dict[str, Any]] = []
    flagged: set[str] = set()

    # 1) 용어표와의 불일치 — 용어표에 한글 표기가 있는 항목만 판정 대상이다.
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        if e.get("en"):
            by_key[_norm_en(str(e["en"]))].append(e)
        if e.get("abbr"):
            by_key[_norm_en(str(e["abbr"]))].append(e)
    for key, kos in seen.items():
        cands = [e for e in by_key.get(key, []) if e.get("ko")]
        if not cands:
            continue
        table_kos = {str(e["ko"]).strip() for e in cands}
        # 용어표 자체가 상충이면 불일치 판정을 보류한다 — 오염된 표를 잣대로 쓰면
        # 올바른 본문 표기를 벌한다(2026-08-28 v7 실측: 문장 조각 채굴판 "재생에너지
        # 사용 확인"이 잣대가 돼 정상 표기 "RE100"이 불일치로 찍혔다). 대신 표 상충
        # 자체를 경고해 사람이 정본을 고르게 한다(주입 쪽 보수화와 대칭).
        if len(_collapse_variants({ko: [] for ko in table_kos})) > 1:
            srcs = " · ".join(
                f'"{e["ko"]}"({str(e.get("source_title") or "").strip() or "?"})'
                for e in cands[:_MAX_SAMPLES]
            )
            body = " / ".join(f'"{k}"' for k in list(kos)[:3])
            label = next(e.get("en") or e.get("abbr") for e in cands)
            chapter, ref = first_at.get(key, (0, ""))
            out.append(
                _finding(
                    chapter,
                    ref,
                    "warning",
                    "용어표 상충",
                    f"{label}의 한글 표기가 자료 간에 갈린다: {srcs} - 본문은 {body} 사용 중,"
                    " 정본 표기 확인 필요",
                )
            )
            flagged.add(key)
            continue
        # 대조도 요동 병합과 같은 눈금 — 띄어쓰기 차이·문맥 접미("자료 기준 재생에너지
        # 공급인증서")를 불일치로 오인하면 안 된다.
        table_bares = {t.replace(" ", "") for t in table_kos}
        for body_ko, refs in kos.items():
            bare = body_ko.replace(" ", "")
            if bare in table_bares or any(
                len(t) >= _MIN_ABSORB_CHARS and bare.endswith(t) for t in table_bares
            ):
                continue
            src = next((str(e.get("source_title") or "").strip() for e in cands if e.get("ko")), "")
            label = next(e.get("en") or e.get("abbr") for e in cands)
            chapter, _ = first_at.get(key, (0, refs[0]))
            out.append(
                _finding(
                    chapter,
                    refs[0],
                    "warning",
                    "용어 표기 불일치",
                    f'본문 "{body_ko}({label})" ↔ 용어표 "{" / ".join(sorted(table_kos))}"'
                    + (f"({src})" if src else "")
                    + f" - 표기 통일 확인 필요, {len(refs)}개 절: {', '.join(refs[:_MAX_SAMPLES])}"
                    + (" …" if len(refs) > _MAX_SAMPLES else ""),
                )
            )
            flagged.add(key)

    # 2) 보고서 안 표기 요동 — 같은 원어의 한글 표기가 두 가지 이상(병합 후에도).
    variance_rows: list[tuple[int, dict[str, Any]]] = []
    for key, kos in seen.items():
        collapsed = _collapse_variants(kos)
        if len(collapsed) < 2:
            continue
        chapter, ref = first_at.get(key, (0, ""))
        variants = " · ".join(
            f'"{ko}"({", ".join(refs[:2])})' for ko, refs in sorted(collapsed.items())
        )
        variance_rows.append(
            (
                len(collapsed),
                _finding(
                    chapter,
                    ref,
                    "warning",
                    "용어 표기 요동",
                    f"같은 원어의 한글 표기가 {len(collapsed)}가지: {variants} - 하나로 통일 필요",
                ),
            )
        )
        flagged.add(key)
    variance_rows.sort(key=lambda x: -x[0])
    out.extend(f for _, f in variance_rows[:_MAX_VARIANCE_ROWS])
    if len(variance_rows) > _MAX_VARIANCE_ROWS:
        out.append(
            _finding(
                0,
                "",
                "warning",
                "용어 표기 요동",
                f"그 외 표기 요동 용어 {len(variance_rows) - _MAX_VARIANCE_ROWS}개 - 변형이"
                " 많은 순으로 위에 실었다",
            )
        )

    # 3) 정의 용어 표기 검토 — 자료가 정의한 용어의 본문 표기를 사람이 훑을 한 행.
    #    한글 뜻이 정의(대개 영문)와 맞는지는 기계로 못 가른다 — 목록만 세운다.
    defined: dict[str, str] = {}
    for e in entries:
        if not e.get("definition"):
            continue
        for k in (e.get("en"), e.get("abbr")):
            if k:
                defined.setdefault(_norm_en(str(k)), str(e.get("en") or e.get("abbr")))
    review: list[str] = []
    review_first: tuple[int, str] | None = None
    for key, kos in seen.items():
        if key not in defined or key in flagged:
            continue
        for body_ko, refs in kos.items():
            review.append(f'"{body_ko}({defined[key]})" [{refs[0]}]')
            if review_first is None:
                review_first = first_at.get(key)
    if review:
        chapter, ref = review_first or (0, "")
        out.append(
            _finding(
                chapter,
                ref,
                "warning",
                "정의 용어 표기 검토",
                f"자료가 정의한 용어의 본문 표기 {len(review)}건 - 뜻이 자료 정의와 맞는지"
                f" 확인: {' · '.join(review[:_MAX_REVIEW_TERMS])}"
                + (" …" if len(review) > _MAX_REVIEW_TERMS else ""),
            )
        )
    return out
