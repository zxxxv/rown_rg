"""보고서 후보의 정적 게이트 — 순수 결정적 검사 (LLM·DB 없음).

설계 불변식: AI는 후보를 생성만 하고, 합격/불합격은 여기의 코드가 결정한다.
LLM-judge를 쓰지 않으므로 무한 루프·비결정성이 원천 차단된다. 각 검사는
GateResult(check, severity, passed, detail)를 돌려주고, HARD 실패는 후보를
제외(사람에게 안 보임), SOFT 실패는 경고로 사람에게 표시된다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import UUID

import structlog

from src.core.citations import MARK_RE, numbers_in_order
from src.core.types import (
    CheckSeverity,
    GateResult,
    RetrievedChunk,
    SectionCandidate,
    SectionCandidateSet,
    SectionDraft,
    SectionPlan,
    StaticCheckReport,
)

logger = structlog.get_logger(__name__)

# 기본 길이 경계 (문자 수) — 필요하면 호출 시 오버라이드.
DEFAULT_MIN_CHARS = 200
DEFAULT_MAX_CHARS = 4000

# 미완성 초안에서 흔한 잔여 placeholder 토큰.
_PLACEHOLDER_TOKENS: tuple[str, ...] = ("{{", "}}", "[[", "]]", "TODO", "TBD", "XXX", "<채워넣기>")

# 숫자·비율 토큰: 앞자리 숫자 + 선택적 천단위 콤마 + 선택적 소수부 + 선택적 %.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

# 비표준 인용 마커 — 대괄호 표기 중 정상 [n]이 아닌 것(2026-08-05 실측:
# '[배경자료 제공됨]', '[배경 맥락]', '[근거 없음 - …]' 등 모델이 발명한 표기).
# 마크다운 링크 [텍스트](url)와 그림/표 캡션([그림 1-1])은 정상 표기라 제외한다.
# [n]은 직접 인용 표기라 계속 허용한다 — 참고 표기는 (출처 n)으로 따로 쓴다
# (src/core/citations.py). 대괄호 남용은 아래 check_quote_marks가 따로 본다.
_BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,40})\](?!\()")
_ALLOWED_BRACKET_RE = re.compile(r"^(?:\d+|그림\s?[\d\-. ]+|표\s?[\d\-. ]+)$")

# 제어문자 (렌더 불가 신호) — 탭·개행·캐리지리턴(\x09-\x0d 중 일부)은 허용.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def check_citation_resolves(draft: SectionDraft, valid_chunk_ids: set[UUID]) -> GateResult:
    """draft가 인용한 chunk_id가 전부 근거 풀(검색 결과)에 실재하는지. (hallucinated 인용 차단)"""
    unresolved = [cid for cid in draft.cited_chunk_ids if cid not in valid_chunk_ids]
    passed = not unresolved
    detail = None
    if not passed:
        preview = ", ".join(str(c) for c in unresolved[:3])
        detail = f"근거 풀에 없는 인용 {len(unresolved)}건: {preview}"
    return GateResult(
        check="citation_resolves",
        severity=CheckSeverity.HARD,
        passed=passed,
        detail=detail,
    )


def check_complete(draft: SectionDraft) -> GateResult:
    """생성이 정상 종료로 완결됐는지 — max_tokens 컷·refusal 등은 HARD 제외.

    길이 검사(bounds)는 SOFT라 문장 중간에 끊긴 토막도 통과한다 — 216자 토막이
    '완성 절'로 조립된 실사고(2026-08-13, 7.1절)의 구멍. 미완결 초안은 후보에서
    제외해 write 루프의 재생성을 태우고, 그래도 실패하면 절 누락으로 표면화된다.
    """
    passed = not draft.incomplete_reason
    return GateResult(
        check="complete",
        severity=CheckSeverity.HARD,
        passed=passed,
        detail=None if passed else f"생성 미완결 (stop_reason={draft.incomplete_reason})",
    )


def check_renderable(draft: SectionDraft) -> GateResult:
    """본문이 렌더 가능한지 — 비어있지 않고 제어문자가 없어야.

    지금은 텍스트 sanity만 검사한다. HWPX 직렬화가 준비되면 여기서 실제 직렬화
    성공 여부를 검사하도록 확장한다(TODO: HWPX serialize seam).
    """
    if not draft.content.strip():
        return GateResult(
            check="renderable",
            severity=CheckSeverity.HARD,
            passed=False,
            detail="본문이 비어 있음",
        )
    ctrl = _CONTROL_RE.search(draft.content)
    if ctrl is not None:
        return GateResult(
            check="renderable",
            severity=CheckSeverity.HARD,
            passed=False,
            detail=f"렌더 불가 제어문자 포함 (U+{ord(ctrl.group()):04X})",
        )
    return GateResult(check="renderable", severity=CheckSeverity.HARD, passed=True)


def _normalize_number(token: str) -> str:
    """콤마·공백 제거 + 후행 % 제거 — 매칭용 정규화."""
    return token.replace(",", "").replace(" ", "").rstrip("%")


# ── 한↔영 자릿수 환산 ──────────────────────────────────────────────────────
# 영문 코퍼스에서 수치 검출기가 통째로 무력해진 원인(2026-08-24 COMPA 실측:
# 정밀도 14.8%). 본문은 "70.5억 달러"라고 쓰고 근거는 "USD 7.05 billion"이라 적는다.
# 콤마만 지우는 부분문자열 대조로는 영영 만나지 못해 전부 '무근거'로 떨어졌다.
_KOR_SCALE: dict[str, int] = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3}
_KOR_NUM_PART = r"\d[\d,]*(?:\.\d+)?\s*[조억만천]"
# 자리 단위를 이어 쓴 합성 표기("2억 450만")까지 한 토큰으로 본다 — 쪼개 읽으면
# 2와 450이 되어 코퍼스의 "204.5 million"과 절대 안 맞고, 450은 주입 의심으로 샌다.
_KOR_NUMBER_RE = re.compile(rf"{_KOR_NUM_PART}(?:\s*{_KOR_NUM_PART})*")
# 영문 자릿수 표기 — 값을 이 단위로 환산한 가수(mantissa)를 후보로 만든다.
_EN_SCALES: tuple[float, ...] = (10**12, 10**9, 10**6, 10**3)


def korean_magnitude(token: str) -> float | None:
    """한국어 큰 수 표기의 값 — "2억 450만" → 204500000.0. 아니면 None."""
    text = token.replace(",", "")
    total, last, found = 0.0, None, False
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([조억만천])", text):
        scale = _KOR_SCALE[m.group(2)]
        if last is not None and scale >= last:
            return None  # 자리 단위가 내림차순이 아니면 한 수가 아니다
        total += float(m.group(1)) * scale
        last, found = scale, True
    return total if found else None


def _trim(value: float) -> str:
    """소수 꼬리 0을 걷은 표기 — 7.05·46.1·204.5처럼 근거에 적히는 꼴."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def number_variants(token: str) -> list[str]:
    """이 수치가 근거에 적혔을 만한 표기들(정규화된 문자열).

    한국어 큰 수는 영문 자릿수로 환산한 가수까지 후보에 넣는다: "70.5억"이면
    7.05(billion)·70500000000, "4,610만"이면 46.1(million)·46100000.
    """
    norm = _normalize_number(token)
    out = [norm]
    value = korean_magnitude(token)
    if value is None:
        return out
    out.append(_trim(value))  # 자리 단위를 푼 온전한 숫자
    for scale in _EN_SCALES:
        mantissa = value / scale
        if 0.1 <= mantissa < 1000:
            out.append(_trim(mantissa))
    # 공백을 넣어 쓴 한국어 표기도 근거에 있을 수 있다("2억 450만").
    spaced = re.sub(r"([조억만천])(\d)", r"\1 \2", norm)
    if spaced != norm:
        out.append(spaced)
    return list(dict.fromkeys(out))


def number_in_text(token: str, haystack_norm: str) -> bool:
    """수치가 근거(정규화 문자열)에 있는가 — 자릿수 환산 표기까지 본다."""
    return any(v and v in haystack_norm for v in number_variants(token))


def normalize_haystack(text: str) -> str:
    """근거 대조용 정규화 — 콤마·강조 기호를 걷는다(공백은 남긴다)."""
    return text.replace(",", "").replace("*", "")


def numeric_mentions(text: str) -> list[str]:
    """근거 대조에 쓸 수치 표기 — 한국어 큰 수는 자리 단위까지 한 덩이로 본다.

    significant_numbers는 맨 숫자만 돌려준다(표 검사·정렬 표시가 그 규약을 쓴다).
    근거 대조는 자리 단위를 알아야 환산이 되므로 여기서 따로 집는다: "2억 450만"을
    2와 450으로 쪼개 읽으면 근거의 "204.5 million"과 영영 못 만나고, 남은 450은
    엉뚱한 자료에 붙어 주입 의심으로 샌다(2026-08-24 COMPA 실측).
    """
    out: list[str] = []
    # 한국어 큰 수를 먼저 집고 그 자리를 공백으로 덮는다 — 남은 글에서 맨 숫자를
    # 평소 규칙대로 뽑되(앞뒤 문맥 규칙이 그대로 산다) 큰 수의 조각은 빠진다.
    masked = list(text)
    for m in _KOR_NUMBER_RE.finditer(text):
        token = m.group().strip()
        if token not in out:
            out.append(token)
        for i in range(m.start(), m.end()):
            masked[i] = " "
    for token in _significant_numbers("".join(masked)):
        if token not in out:
            out.append(token)
    return out


def _is_year(token: str) -> bool:
    """1900~2099의 네 자리 정수 — 연도 표기로 본다."""
    if "." in token or "%" in token:
        return False
    digits = token.replace(",", "")
    return len(digits) == 4 and digits.isdigit() and 1900 <= int(digits) <= 2099


_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")


def claim_years(text: str) -> tuple[str, ...]:
    """주장 단위 속 명시 연도(1900~2099, 등장 순서·중복 제거).

    수치 검사에서 빠지는 연도를 여기서만 되집는다 — 사전지식 주입 가드의 짝.
    '2024년 기준 428개사'의 2024를 집어, 428이 코퍼스에서 그 연도 곁에 실재하는지
    볼 수 있게 한다(연도 없는 수치 창작은 다른 검출기 몫).
    """
    out: list[str] = []
    for m in _YEAR_TOKEN_RE.finditer(text):
        prev = text[m.start() - 1] if m.start() > 0 else ""
        nxt = text[m.end()] if m.end() < len(text) else ""
        # 더 긴 수의 조각(52024)이나 소수 꼬리(3.2024)는 연도가 아니다.
        if prev.isdigit() or prev == "." or nxt.isdigit():
            continue
        if m.group() not in out:
            out.append(m.group())
    return tuple(out)


# 연.월 표기 — "2019.12"·"2021.6". 소수 꼴이라 연도 제외를 비켜가 '무근거 수치'
# 오탐의 최다 원천이었다(2026-08-14 실측: 탄소규제 런 critical 26건 중 표본 22건이
# 오탐, 다수가 연월·아포스트로피 축약 연도).
_YEAR_MONTH_RE = re.compile(r"^(?:19|20)\d{2}\.(?:1[0-2]|0?[1-9])$")
# 축약 연도 표기의 접두 문자 — "’25"·"’25.10"의 25는 수치가 아니라 2025년이다.
_DATE_PREFIXES = "’'‘′`"


def _significant_numbers(text: str) -> list[str]:
    """본문에서 '사실 주장'으로 볼 만한 숫자만 추출.

    구조적 소수(1개·2장 등 한 자리)는 오탐이 많아 건너뛰고, 두 자리 이상이거나
    소수/퍼센트를 포함한 토큰만 검사 대상으로 삼는다.

    연도(2029)는 뺀다. 원문은 같은 해를 '29년·2029.·`24~`26처럼 다르게 적어 부분문자열
    매칭이 자주 빗나가는데, 화면에는 "근거에 없는 수치"로 뜬다(2026-08-11 실측: 경고
    5건 중 3건이 연도). 같은 이유로 연.월(2019.12)과 아포스트로피 축약(’25.10)도
    뺀다 — 날짜는 수치 주장이 아니고, 날짜 창작은 근거 동봉 판정이 문맥으로 본다.
    pm_verify가 중복 인용 검사에서 연도를 뺀 것과 같은 이유다.
    """
    out: list[str] = []
    excluded: list[tuple[str, str]] = []
    for m in _NUMBER_RE.finditer(text):
        token = m.group()
        digits = _normalize_number(token).replace(".", "")
        # 절·장·항 번호는 수치 주장이 아니라 문서 안 길찾기다("1.1절 참조").
        tail = text[m.end() : m.end() + 1]
        if tail in ("절", "장", "항") and not token.endswith("%"):
            excluded.append((token, "section_ref"))
            continue
        if _is_year(token):
            excluded.append((token, "year"))
            continue
        if _YEAR_MONTH_RE.match(token):
            excluded.append((token, "year_month"))
            continue
        # ’25·’25.10 — 아포스트로피 바로 뒤 숫자는 축약 연도(연.월)다. %가 붙었으면 수치.
        if m.start() > 0 and text[m.start() - 1] in _DATE_PREFIXES and not token.endswith("%"):
            excluded.append((token, "apostrophe_date"))
            continue
        # 라틴 문자에 붙은 숫자는 식별자 조각(RE100·B2B·S2)이지 수치 주장이 아니다
        # (2026-08-14 실측: 캡션 미포착 37건 중 36건이 'RE100'의 100).
        prev = text[m.start() - 1] if m.start() > 0 else ""
        if prev.isascii() and prev.isalpha():
            excluded.append((token, "term_digit"))
            continue
        # '년'이 바로 붙는 숫자는 기간·연차 표기(10년차·30년간) — 연도류와 같은 이유.
        nxt = text[m.end()] if m.end() < len(text) else ""
        if nxt == "년" and not token.endswith("%"):
            excluded.append((token, "year_suffix"))
            continue
        if "." in token or "%" in token or len(digits) >= 2:
            out.append(token)
    if excluded:
        # 제외는 조용히 버리지 않는다 — 사유별 집계를 남겨야 나중에 "제외 패턴이 진짜
        # 수치를 먹었는가"(미탐율)를 셀 수 있다(2026-08-14 회귀셋 라벨링 방침).
        logger.debug(
            "significant_numbers.excluded",
            n=len(excluded),
            samples=[f"{t}({r})" for t, r in excluded[:8]],
        )
    return out


def significant_numbers(text: str) -> list[str]:
    """공개 진입점 - 근거 위치 표시(alignment)가 무근거 판정과 같은 추출을 쓰게 한다."""
    return _significant_numbers(text)


def normalize_number(token: str) -> str:
    """공개 진입점 - significant_numbers와 짝. 매칭은 반드시 이 정규화를 거친다."""
    return _normalize_number(token)


def _numeric_value(token: str) -> float | None:
    """토큰의 수치 값 — 한국어 자리 단위·콤마·%를 푼다."""
    value = korean_magnitude(token)
    if value is not None:
        return value
    try:
        return float(_normalize_number(token))
    except ValueError:
        return None


# 파생치 허용 오차 — 본문은 "약 3.9배"처럼 반올림해 적는다(24.12÷6.16=3.916).
_DERIVED_TOLERANCE = 0.02


def derived_numbers(text: str) -> set[str]:
    """같은 주장 안의 다른 두 수로 계산되는 수 — 근거에 없어도 창작이 아니다.

    "CAGR 24.12%는 6.16% 대비 약 3.9배"의 3.9가 그렇다. 근거에는 24.12와 6.16만
    있으므로 3.9는 늘 '무근거'로 떨어졌다(2026-08-24 COMPA 실측). 계산으로 설명되면
    검사 대상에서 뺀다 — 계산이 맞는지는 산술 검증(arithmetic_suspects)의 몫이다.
    """
    tokens = numeric_mentions(text)
    pairs = [(t, v) for t in tokens if (v := _numeric_value(t)) is not None]
    out: set[str] = set()
    for token, value in pairs:
        if value == 0:
            continue
        for i, (ta, a) in enumerate(pairs):
            for tb, b in pairs[i + 1 :]:
                if token in (ta, tb) or a == 0 or b == 0:
                    continue
                for candidate in (a / b, b / a, a + b, a - b, b - a, a * b):
                    if abs(candidate - value) <= abs(value) * _DERIVED_TOLERANCE:
                        out.add(_normalize_number(token))
                        break
    return out


def ungrounded_numbers(content: str, cited_content: str) -> list[str]:
    """본문에서 인용 근거에 없는 유의미한 숫자 토큰(등장 순서·중복 제거).

    게이트(생성 시점)와 편집 화면(조회 시점)이 같은 판정을 쓰도록 분리했다 —
    화면에서 "이 절의 근거 미확인 수치"를 그대로 보여주기 위함(2026-08-09).
    퍼지 매칭(콤마 정규화 후 부분문자열)이라 오탐 여지가 있어 경고용이다.
    """
    haystack = normalize_haystack(cited_content)
    out: list[str] = []
    seen: set[str] = set()
    # 제목·표 줄은 주장이 아니다 - 소제목 번호("1.1 사업 배경")가 수치로 잡혀 화면에
    # 올라왔다. 주장 단위로 좁혀 본다(근거 추적·미인용 검사와 같은 눈금).
    # 인용 마커 자체도 걷어낸다 - "(출처 31)"의 31이 수치로 잡혀 "근거에서 확인되지
    # 않는 수치 1개: 31"이 떴다(2026-08-12 검증 런 화면). 출처 번호는 주장이 아니고,
    # 마커가 많이 붙은 절일수록 경고가 늘어 진짜 신호를 덮는다.
    units = [MARK_RE.sub(" ", u) for u in claim_units(content)]
    # 파생치는 주장 단위 안에서만 성립한다 — 절 전체를 섞으면 무관한 두 수의 우연한
    # 비율이 실수치를 덮는다.
    derived = {d for unit in units for d in derived_numbers(unit)}
    for token in numeric_mentions("\n".join(units)):
        norm = _normalize_number(token)
        if norm in seen:
            continue
        seen.add(norm)
        if norm and norm not in derived and not number_in_text(token, haystack):
            out.append(token)
    return out


def misattributed_numbers(
    content: str,
    marker_chunks: dict[int, Sequence[UUID]],
    chunk_texts: dict[UUID, str],
) -> list[str]:
    """마커가 가리킨 근거엔 없는 수치가 같은 풀의 다른 청크에 있는 경우 — 오귀속 신호.

    유령 출처 검사(참고문헌 범위 밖 번호)는 '목록에 존재하는 번호로 잘못 가리키는'
    마커를 통과시키고, claim_verify는 근거 집합을 마커와 무관하게 받아 도메인 밖이다
    (2026-08-14 프로브 실증: 합성 오귀속 5건을 세 모델 전원이 supported로 통과, 0/5).
    마커 dedup 147건·이중 색인 재번호·유령 출처 실측이 전부 번호가 어긋날 수 있다는
    증거라 실제 위험이다. 문장 단위로 "인용 청크에 없는 수치가 풀의 다른 청크에는
    있다"를 결정적으로 대조한다 — 어디에도 없으면 무근거(다른 검사 몫), 다른 데
    있으면 마커가 엉뚱한 곳을 가리킨다는 뜻이다.
    """
    out: list[str] = []
    seen: set[str] = set()
    pool = {cid: normalize_haystack(t or "") for cid, t in chunk_texts.items()}
    for unit in claim_units(content):
        marks = numbers_in_order(unit)
        if not marks:
            continue
        cited_ids = {cid for n in marks for cid in marker_chunks.get(n, ())}
        if not cited_ids:
            continue
        bare = MARK_RE.sub(" ", unit)
        cited_text = "\n".join(pool.get(c, "") for c in cited_ids)
        # 인용 근거가 외국어면 어휘로 '없다'를 선언할 수 없다(단위 환산 - 72억 vs
        # $7.2B). 그 축의 원칙 그대로 오귀속 판정도 건너뛴다.
        if cited_text.strip() and not _HANGUL_RE.search(cited_text):
            continue
        derived = derived_numbers(bare)
        for tok in numeric_mentions(bare):
            norm = _normalize_number(tok)
            if not norm or norm in seen or norm in derived or number_in_text(tok, cited_text):
                continue
            elsewhere = next(
                (
                    cid
                    for cid, text in pool.items()
                    if cid not in cited_ids and number_in_text(tok, text)
                ),
                None,
            )
            if elsewhere is not None:
                seen.add(norm)
                mark_s = ", ".join(str(n) for n in sorted(set(marks))[:3])
                out.append(f"{tok} — 인용(출처 {mark_s})엔 없고 풀의 다른 근거에 있음")
    return out


# ── 산술 검증 — 파생 계산은 근거 검사에서 빼는 게 아니라 계산 자체를 검증한다 ──
# (2026-08-14 방침: "30.6+18.1을 합한 48.7%"를 근거 검사에서 제외만 하면, 합을
# 틀리거나 합치면 안 되는 값을 더한 경우가 조용히 통과한다. 정밀도 우선 — 피연산자와
# 결과가 같은 주장 단위에 함께 있을 때만 판정한다.)

# "A(단위)에서 B(단위)로 p% 증가/감소" — 실측 결함(2026-08-14 탄소규제 런 1.2):
# "전년 240TWh에서 289TWh로 30% 증가"는 실제 +20.4%다. %p 표기는 뒤에 p가 붙어
# dir 매칭이 깨지므로 자동으로 제외된다(퍼센트포인트는 이 산식이 아니다).
_RATE_CLAIM_RE = re.compile(
    r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[%A-Za-z가-힣]{0,6})\s*에서\s*"
    r"(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P=unit)?[^\d\n]{0,12}?(?:로|으로)\s*"
    r"[^\d\n]{0,16}?(?P<p>\d[\d,]*(?:\.\d+)?)\s*%\s*(?:가까이\s*|이상\s*|넘게\s*)?"
    r"(?P<dir>증가|성장|상승|확대|늘|감소|줄|하락|축소)"
)
_SUM_KEYWORD_RE = re.compile(r"합한|합치면|합하면|더한")
_SUM_RESULT_WINDOW = 30  # 키워드 뒤 이 거리 안의 첫 숫자를 '합산 결과'로 본다


def _to_float(token: str) -> float | None:
    try:
        return float(_normalize_number(token))
    except ValueError:
        return None


def _sum_subset_matches(operands: list[float], target: float) -> bool:
    """피연산자 2~3개의 합이 target과 맞는 조합이 있는가 (반올림 오차 허용)."""
    tol = max(0.11, abs(target) * 0.005)
    n = len(operands)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(operands[i] + operands[j] - target) <= tol:
                return True
            for k in range(j + 1, n):
                if abs(operands[i] + operands[j] + operands[k] - target) <= tol:
                    return True
    return False


def arithmetic_suspects(content: str) -> list[str]:
    """주장 단위 안의 파생 계산이 스스로와 맞는지 — 틀린 곳의 설명 목록 (순수 함수).

    증가율: "A에서 B로 p% 증가"의 p를 (B-A)/A와 대조(허용 오차 max(2%p, p의 10%)).
    합산: "…를 합한 C"의 C가 같은 문장 피연산자 2~3개 합과 맞는지. 결과·피연산자가
    한 문장에 함께 없으면 판정하지 않는다(정밀도 우선).
    """
    out: list[str] = []
    for unit in claim_units(content):
        bare = MARK_RE.sub(" ", unit)
        for m in _RATE_CLAIM_RE.finditer(bare):
            a, b, p = _to_float(m.group("a")), _to_float(m.group("b")), _to_float(m.group("p"))
            if a is None or b is None or p is None or a == 0:
                continue
            change = (b - a) / a * 100.0
            if m.group("dir") in ("감소", "줄", "하락", "축소"):
                change = -change
            if change < 0 or abs(change - p) > max(2.0, 0.1 * p):
                out.append(
                    f"{m.group('a')}→{m.group('b')}는 {change:+.1f}%인데 "
                    f"{m.group('p')}% {m.group('dir')}로 서술"
                )
        kw = _SUM_KEYWORD_RE.search(bare)
        if kw is None:
            continue
        after = bare[kw.end() : kw.end() + _SUM_RESULT_WINDOW]
        result_m = _NUMBER_RE.search(after)
        if result_m is None:
            continue
        target = _to_float(result_m.group())
        if target is None:
            continue
        operands = [
            v
            for t in _significant_numbers(bare[: kw.start()])
            if (v := _to_float(t)) is not None and v != target
        ]
        if len(operands) >= 2 and not _sum_subset_matches(operands, target):
            out.append(f'합산 불일치 의심: "{unit.strip()[:40]}…"')
    return out


def check_numeric_grounded(draft: SectionDraft, cited_content: str) -> GateResult:
    """본문의 유의미한 숫자가 인용된 근거 청크 본문에 실제로 등장하는지.

    퍼지 매칭(콤마 정규화 후 부분문자열)이라 오탐 여지가 있어 SOFT — 미매칭
    숫자는 제외 사유가 아니라 사람에게 넘기는 '확인 요망' 플래그다.
    """
    ungrounded = ungrounded_numbers(draft.content, cited_content)
    passed = not ungrounded
    detail = None
    if not passed:
        preview = ", ".join(ungrounded[:5])
        detail = f"근거에서 확인 안 되는 숫자 {len(ungrounded)}건: {preview}"
    return GateResult(
        check="numeric_grounded",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=detail,
    )


# 문장 분리 — 마침표·물음표·느낌표 뒤 공백. 개조식은 줄 자체가 한 단위라 줄 먼저 나눈다.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# 본문이 아닌 줄: 제목·표·인용블록·구분선.
_NON_CLAIM_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s|\||>|[-*_]{3,}\s*$)")
# 캡션·표 메타 줄 — "표: …"·"그림 3-1: …"·"(단위: %)"는 주장이 아니라 분모를 오염시킨다
# (2026-08-14 실측: 미포착 수치 111건 중 34건이 캡션). 번호부("표 3:"·"표 3-1:") 허용.
_CAPTION_LINE_RE = re.compile(r"^\s*(?:(?:표|그림)\s*[\d\-. ]*[::]|\(단위)")
# 캡션 수치 검사용 — (단위…)는 정의상 단위 선언이라 표·그림 캡션만 본다.
_TABLE_FIGURE_CAPTION_RE = re.compile(r"^(?:표|그림)\s*[\d\-. ]*[::]")
# 글머리 기호(개조식) — 판정에서 떼어내고 길이를 잰다. 'ㅇ'·'ㅁ'은 한글 자모 마커다.
_BULLET_RE = re.compile(r"^\s*(?:[-*•‣◦○□▪ㅇㅁ]|\d+[.)]|[가-힣][.)])\s+")
# 주장으로 볼 최소 길이 — 이보다 짧은 줄은 소제목·나열 항목이라 인용을 요구하지 않는다.
_MIN_CLAIM_CHARS = 25
# 서술을 끝맺는 꼬리 — 개조식 소제목("…확보 전략 제언")과 주장("…취약성에서 발생하고 있음")을
# 가르는 실측 기준(2026-08-11, 예타 6.2절). 제목은 명사로 끝나고 주장은 종결형으로 끝난다.
# 이 검사는 사람에게 보내는 경고라 재현율보다 정밀도가 중요하다 — 애매하면 세지 않는다.
_CLAIM_TAILS: tuple[str, ...] = (
    "다",
    "음",
    "임",
    "함",
    "됨",
    "짐",
    "옴",  # "…분석이 나옴" — 명사형 어미 -ㅁ은 앞 모음에 따라 음/옴으로 갈린다
    # "…30% 증가하였으며 … 5% 늘어남" 꼴이 통째로 주장에서 빠져 산술·근거 검사가
    # 못 보던 구멍(2026-08-14 실측: 탄소규제 런 1.2). '남'은 "베트남"류 지명 소제목
    # 오탐 여지가 있으나 나타남·늘어남·드러남 빈도가 압도적이라 받는다.
    "남",
    "듦",  # "…줄어듦"
    "필요",
    "전망",
    "예상",
    "우려",
    "가능",
    "요구",
    "시급",
    "중요",
)
_SENTENCE_END_RE = re.compile(r"[.!?]\s*$")


def _candidate_units(content: str) -> list[tuple[str, str]]:
    """주장 후보 (원문, 마커 뗀 문장) 목록 — 제목·표·펜스·짧은 나열만 거른 상태.

    여기서 살아남은 뒤 종결형 검사에서 떨어지는 문장이 '검출 파이프라인에서 증발한
    분모'다 — claim_units와 claim_coverage가 같은 후보를 봐야 커버리지가 성립한다.
    """
    out: list[tuple[str, str]] = []
    in_fence = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            # 코드펜스(차트 스펙 등) 안은 본문이 아니다 — 'type: bar' 같은 줄을
            # 주장으로 세면 근거 없는 주장 경고가 헛돈다.
            in_fence = not in_fence
            continue
        if in_fence or not line or _NON_CLAIM_LINE_RE.match(line) or _CAPTION_LINE_RE.match(line):
            continue
        body = _BULLET_RE.sub("", line)
        for unit in _SENTENCE_SPLIT_RE.split(body):
            unit = unit.strip()
            # 판정은 마커를 뗀 문장으로 한다 — 개조식은 "…성장했음 [3]"처럼 마커로
            # 끝나는 줄이 대부분이라, 원문 그대로 보면 종결형 검사에서 전부 탈락한다
            # (2026-08-11 실측: 인용 340개짜리 절의 주장이 0건으로 잡혔다).
            bare = MARK_RE.sub("", unit).strip()
            if len(bare) < _MIN_CLAIM_CHARS:
                continue
            out.append((unit, bare))
    return out


# 문장 끝의 짧은 보조 괄호 — "…나타남(복수응답)"·"…됨(’23년 기준)"이 꼬리 검사를
# 비켜가지 않게 벗기고 다시 본다.
_TRAILING_PAREN_RE = re.compile(r"\([^()]{1,30}\)$")
_HANGUL_RE = re.compile(r"[가-힣]")


def _is_claim(bare: str) -> bool:
    if _SENTENCE_END_RE.search(bare) or bare.endswith(_CLAIM_TAILS):
        return True
    stripped = _TRAILING_PAREN_RE.sub("", bare).rstrip()
    if stripped != bare and (_SENTENCE_END_RE.search(stripped) or stripped.endswith(_CLAIM_TAILS)):
        return True
    # 유의미 수치를 실은 한글 문장은 꼬리와 무관하게 주장이다 — 개조식 명사 종결("…약
    # 2.5배 수준"·"…로 측정"·"…에 그침")이 모든 검사에서 증발하던 77건(2026-08-14
    # 실측)의 구조적 마감. 연도·연월은 유의미 수치에서 이미 빠져 있어 헤딩 오탐이 없고,
    # 한글 조건은 '영문 줄은 주장이 아니다' 계약(test_alignment 교차언어 원칙)을 지킨다.
    return bool(_HANGUL_RE.search(bare)) and bool(_significant_numbers(bare))


def claim_units(content: str) -> list[str]:
    """본문을 '주장 단위'(문장·개조식 항목)로 자른다 — 인용 여부와 무관하게 전부.

    제목·표·구분선과 짧은 나열 항목, 명사로 끝나는 소제목은 주장이 아니라 제외한다.
    근거 추적(services/qa/alignment)과 미인용 검사가 같은 단위를 봐야 화면의 숫자와
    경고가 어긋나지 않는다 — 그래서 분해를 여기 하나로 둔다.
    """
    return [unit for unit, bare in _candidate_units(content) if _is_claim(bare)]


def uncovered_units(content: str) -> list[str]:
    """주장 후보였지만 종결형 검사에서 떨어진 줄 — 어떤 검사도 보지 않는 본문.

    claim_units가 못 집은 줄은 근거 대조·무근거 수치·산술 검사에서 통째로 증발하는데,
    화면에는 그 사실이 안 나타났다. 사람은 "왜 이 줄만 표시가 없지"를 알 수가 없다
    (2026-08-26 지적). 목록으로 내려 화면이 짚어 줄 수 있게 한다.

    claim_coverage가 세는 것과 같은 후보·같은 판정을 쓴다 — 숫자와 목록이 어긋나면
    둘 중 하나는 거짓말이 된다.
    """
    return [unit for unit, bare in _candidate_units(content) if not _is_claim(bare)]


def claim_coverage(content: str) -> tuple[int, int, list[str]]:
    """(픽업 주장 수, 후보 문장 수, 미포착 중 수치 포함 문장) — 분모를 드러내는 지표.

    claim_units가 못 집은 문장은 근거 대조·무근거·산술 검사 전부에서 증발하는데,
    그 손실은 정밀도·재현율 어디에도 안 나타난다(지표는 들어온 문장에 대해서만
    계산된다). '남' 꼬리 누락으로 산술 결함이 통째로 안 보이던 실사고(2026-08-14)의
    재발 방지 — 미포착 수치 문장 목록이 곧 다음 보수 대상이다.
    """
    picked = 0
    total = 0
    missed_numeric: list[str] = []
    for _unit, bare in _candidate_units(content):
        total += 1
        if _is_claim(bare):
            picked += 1
        elif _significant_numbers(bare):
            missed_numeric.append(bare[:60])
    # 캡션 제외는 그 자체가 새 맹점이다 — "표 3: 2030년 감축목표 40%"처럼 캡션 꼴로
    # 수치 주장을 하는 줄은 후보에서 빠져 어떤 검사도 못 본다. 표·그림 캡션에 유의미
    # 수치가 남아 있으면 미포착으로 센다("(단위: …)"는 정의상 단위 선언이라 제외,
    # 연도·연차·용어 숫자는 유의미 수치가 아니라 "…현황(’23년)"·"RE100 비교"는 무해).
    # 이로써 missed_numeric은 동어반복이 아니라 실제 발화 가능한 지표가 된다:
    # 분할 실패·수치 정의 변경·캡션 과잉 제외 셋 다 여기서 드러난다(2026-08-14 지침).
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if _TABLE_FIGURE_CAPTION_RE.match(line) and _significant_numbers(line):
            missed_numeric.append(f"캡션: {line[:60]}")
    return picked, total, missed_numeric


def uncited_units(content: str) -> list[str]:
    """인용 마커가 없는 주장 단위 목록.

    게이트와 화면이 같은 판정을 쓰도록 분리했다(ungrounded_numbers와 같은 규약).
    그것까지 세면 개조식 보고서는 항상 절반이 '미인용'으로 나와 신호가 죽는다.
    """
    return [u for u in claim_units(content) if not MARK_RE.search(u)]


def check_uncited_claims(draft: SectionDraft) -> GateResult:
    """근거 마커가 붙지 않은 주장이 지나치게 많은지.

    마커가 가리키는 청크와 본문이 어긋나는 것은 numeric_grounded가 잡지만, 아예
    마커가 없는 문장은 어떤 검사에도 안 걸려 그대로 통과했다. 모델이 근거 없이
    쓴 대목이 여기서 드러난다. 개조식 특성상 이어지는 항목은 마커를 생략하는 게
    자연스러워 SOFT — 비율이 절반을 넘을 때만 사람에게 알린다.
    """
    units = uncited_units(draft.content)
    total = len(units) + len(MARK_RE.findall(draft.content))
    ratio = len(units) / total if total else 0.0
    passed = len(units) < 3 or ratio <= 0.5
    detail = None
    if not passed:
        preview = " / ".join(u[:30] for u in units[:2])
        detail = f"근거 표기 없는 주장 {len(units)}건({ratio:.0%}): {preview}"
    return GateResult(
        check="uncited_claims",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=detail,
    )


def check_citation_markers(draft: SectionDraft) -> GateResult:
    """인용 표기가 표준([n])인지 — 모델이 발명한 '[배경자료 제공됨]'류 마커 검출.

    배경 요약(인용 불가)을 근거 삼을 때 나오는 오염 신호라 SOFT 경고로 사람에게
    보여준다(후보 제외는 아님 — 본문 자체는 유효할 수 있다).
    """
    found: list[str] = []
    for m in _BRACKET_RE.finditer(draft.content):
        inner = m.group(1).strip()
        if not _ALLOWED_BRACKET_RE.match(inner) and inner not in found:
            found.append(inner)
    passed = not found
    detail = None
    if not passed:
        preview = ", ".join(f"[{t}]" for t in found[:3])
        detail = f"비표준 인용 마커 {len(found)}종: {preview} - 인용은 [숫자]만 허용"
    return GateResult(
        check="citation_markers",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=detail,
    )


# 편집 잔재 — 모델이 본문에 남긴 작성 과정의 흔적(2026-08-14 실측: 탄소규제 런에
# "(… — 삭제)" 메모 2건·"본 파트에서는" 노출·고아 헤딩. 2026-08-15 검증런에서 신형
# 대량 실측: "(출처 17 제외)"류 배정 메모 13건+·오염 마커 "(출превод처 25)"·기형
# <callout> 태그). 문장 자체는 유효할 수 있어 SOFT — 조립 세정(sections/scrub)이
# 같은 패턴을 결정적으로 걷어내고, 여기는 세정 밖 경로(재작성·옛 절)의 가시화 몫이다.
_LEFTOVER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[-—–]\s*삭제\s*\)"), "편집 메모 잔재(… — 삭제)"),
    (re.compile(r"본 파트"), "내부 작성 단위 용어('본 파트')"),
    (re.compile(r"^#+\s*$", re.M), "빈 헤딩(#만 있는 줄)"),
    (
        re.compile(r"\(출처\s*[\d,\s]+\s*(?:은|는)?\s*제외[^()]{0,30}\)"),
        "출처 배정 메모('(출처 n 제외)')",
    ),
    (
        re.compile(r"\(출처\s*[\d,\s]+\s*(?:은|는)?[^()]{0,15}(?:사용\s*불가|미사용)[^()]{0,30}\)"),
        "출처 배정 메모(사용 불가·미사용)",
    ),
    (
        re.compile(r"\(출처\s*[\d,\s]+\s*(?:은|는)?[^()]{0,25}생략[^()]{0,10}\)"),
        "출처 배정 메모(생략)",
    ),
    (re.compile(r"\(출[^\s처()]{1,12}처(?=\s*\d)"), "오염된 출처 마커"),
    (re.compile(r"<callout[^>]*>|</callout\s*>"), "기형 callout 태그(정식은 ::: 펜스)"),
)

# 절단 의심 꼬리 — 수량 표현 뒤 단위 없이 끝나거나("GDP가 약 2"), 접속사로 끝나는 줄.
# 정밀도 우선(_CLAIM_TAILS와 같은 원칙): 애매한 꼴(라틴 약어 RE100·코드 CN 7204)은
# 세지 않는다. 절 끝이 아니라 파트 결합부 중간에 박힌 토막이 표적이다(2026-08-14
# 실측: 탄소규제 런 4.4 "…GDP가 약 2"에서 끊긴 채 다음 항목으로 넘어감).
_TRUNCATED_TAIL_RE = re.compile(
    r"(?:(?:^|\s)(?:약|총|평균|최대|최소)\s*\d[\d,]*(?:\.\d+)?|\s(?:및|또는|그리고))$"
)


def leftover_artifacts(content: str) -> list[str]:
    """본문에 남은 편집 잔재 설명 목록 (순수 함수 — 게이트·PM 경고 공용)."""
    out: list[str] = []
    for pattern, label in _LEFTOVER_PATTERNS:
        if pattern.search(content) and label not in out:
            out.append(label)
    return out


def truncated_lines(content: str) -> list[str]:
    """문장 중간에서 끊긴 것으로 보이는 줄(원문 앞부분) 목록 (순수 함수).

    표·제목·짧은 나열 항목은 제외하고, 주장으로 볼 만한 길이의 줄만 본다.
    """
    out: list[str] = []
    for raw in content.split("\n"):
        line = raw.rstrip()
        if len(line.strip()) < _MIN_CLAIM_CHARS or _NON_CLAIM_LINE_RE.match(line):
            continue
        if _TRUNCATED_TAIL_RE.search(line):
            # 절단 지점은 줄 끝이다 - 머리가 아니라 꼬리를 보여줘야 사람이 바로 찾는다.
            out.append(line.strip()[-40:])
    return out


def check_citation_attribution(draft: SectionDraft, chunks: Sequence[RetrievedChunk]) -> GateResult:
    """작성 시점 마커 오귀속 검사 — 로컬 번호↔cited_chunk_ids 순서 규약으로 대조.

    본문에 등장한 서로 다른 번호의 첫 등장 순서 = cited_chunk_ids 저장 순서
    (candidates._extract_cited_ids, renumber._local_to_global과 같은 규약).
    """
    mapping: dict[int, list[UUID]] = {}
    for i, n in enumerate(numbers_in_order(draft.content)):
        if i >= len(draft.cited_chunk_ids):
            break
        mapping.setdefault(n, []).append(draft.cited_chunk_ids[i])
    found = misattributed_numbers(draft.content, mapping, {c.chunk_id: c.content for c in chunks})
    passed = not found
    return GateResult(
        check="citation_attribution",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=None if passed else f"마커 오귀속 의심 {len(found)}건: {found[0]}",
    )


def check_leftovers(draft: SectionDraft) -> GateResult:
    """편집 잔재·절단 의심 줄 검사 — 본문은 유효할 수 있어 SOFT 경고."""
    problems = leftover_artifacts(draft.content)
    cut = truncated_lines(draft.content)
    if cut:
        problems.append(f'절단 의심 줄 {len(cut)}건 (예: "{cut[0]}…")')
    passed = not problems
    return GateResult(
        check="leftovers",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=None if passed else "; ".join(problems),
    )


def check_bounds(
    draft: SectionDraft,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    forbidden_terms: Sequence[str] = (),
) -> GateResult:
    """길이 경계·금칙어·잔여 placeholder 검사."""
    content = draft.content
    length = len(content.strip())
    problems: list[str] = []
    if length < min_chars:
        problems.append(f"너무 짧음 ({length}자 < {min_chars})")
    if length > max_chars:
        problems.append(f"너무 김 ({length}자 > {max_chars})")
    found_placeholders = [tok for tok in _PLACEHOLDER_TOKENS if tok in content]
    if found_placeholders:
        problems.append(f"잔여 placeholder: {', '.join(found_placeholders)}")
    found_forbidden = [t for t in forbidden_terms if t and t in content]
    if found_forbidden:
        problems.append(f"금칙어: {', '.join(found_forbidden)}")
    passed = not problems
    return GateResult(
        check="bounds",
        severity=CheckSeverity.SOFT,
        passed=passed,
        detail=None if passed else "; ".join(problems),
    )


def run_section_gate(
    draft: SectionDraft,
    chunks: Sequence[RetrievedChunk],
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    forbidden_terms: Sequence[str] = (),
) -> StaticCheckReport:
    """한 섹션 후보에 대해 per-candidate 검사를 모두 돌려 종합 리포트 생성."""
    valid_ids = {c.chunk_id for c in chunks}
    cited_ids = set(draft.cited_chunk_ids)
    cited_content = "\n".join(c.content for c in chunks if c.chunk_id in cited_ids)
    results = [
        check_citation_resolves(draft, valid_ids),
        check_complete(draft),
        check_renderable(draft),
        check_citation_markers(draft),
        check_citation_attribution(draft, chunks),
        check_leftovers(draft),
        check_numeric_grounded(draft, cited_content),
        check_uncited_claims(draft),
        check_bounds(
            draft,
            min_chars=min_chars,
            max_chars=max_chars,
            forbidden_terms=forbidden_terms,
        ),
    ]
    return StaticCheckReport(results=results)


def gate_candidates(
    section_id: UUID,
    drafts: Sequence[SectionDraft],
    chunks: Sequence[RetrievedChunk],
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_chars: int = DEFAULT_MAX_CHARS,
    forbidden_terms: Sequence[str] = (),
) -> SectionCandidateSet:
    """섹션 후보 draft들을 검사해 SectionCandidateSet으로 묶는다."""
    candidates = [
        SectionCandidate(
            draft=d,
            report=run_section_gate(
                d,
                chunks,
                min_chars=min_chars,
                max_chars=max_chars,
                forbidden_terms=forbidden_terms,
            ),
        )
        for d in drafts
    ]
    return SectionCandidateSet(section_id=section_id, candidates=candidates)


def check_structure_complete(
    selected: Sequence[SectionDraft],
    plan: Sequence[SectionPlan],
) -> GateResult:
    """조립 후 보고서 레벨 검사 — 선택된 초안이 계획된 전 섹션을 빠짐없이 덮는지.

    내용이 빈 초안은 '선택됐어도' 누락으로 센다 — 0자 절이 완성 보고서로 마감된
    실사고(2026-08-13, 6.1절) 재발 방지. detail에 절 번호를 실어 사람이 어느 절을
    고쳐야 하는지 바로 알게 한다.
    """
    drafted = {d.section_id for d in selected if d.content.strip()}
    missing = [
        f"{s.chapter_number}.{s.section_number}" for s in plan if s.section_id not in drafted
    ]
    passed = not missing
    detail = None if passed else f"미작성 절 {len(missing)}개: {', '.join(missing)}"
    return GateResult(
        check="structure_complete",
        severity=CheckSeverity.HARD,
        passed=passed,
        detail=detail,
    )
