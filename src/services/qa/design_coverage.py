"""목차 지시 이행 검사 — 확정 목차가 시킨 항목을 본문이 실제로 다뤘는가.

지금까지 이걸 확인하려면 사람이 절마다 목차와 본문을 나란히 놓고 대조해야 했다.
35절이면 아무도 안 한다. 실제로 6.2에서 지시된 위험 항목(부지·인허가·환경·주민
수용성) 중 여럿이 빠진 채 통과했고, 어떤 검사에도 안 걸렸다(2026-08-11).

핵심은 미반영을 찾는 것보다 **원인을 가르는 것**이다:

- 근거 풀에 자료가 있는데 본문에 없다 → 작성 누락. 다시 쓰면 채워진다.
- 근거 풀에도 없다 → 수집 공백. 이 상태로 다시 쓰면 모델이 상식으로 메운다
  (6.2 실측: 자료 0건인 항목을 서술한 문장의 36%가 근거 표기 없음).

둘을 뭉뚱그리면 엉뚱한 걸 고친다 — 그래서 경고 문구도 권고까지 함께 낸다.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from src.services.qa.alignment import NO_SCORE, overlap_score

logger = structlog.get_logger(__name__)

# 항목 나열 구분자 — 목차 지시문이 실제로 쓰는 기호(2026-08-11 실측).
_SPLIT_RE = re.compile(r"[·ㆍ,、]")
# "6.2.2 위험요인 - 인허가·환경·…" 처럼 앞의 절 번호와 라벨을 떼어낸다.
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*\s*")
_LABEL_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
# 방향 지시문 안 괄호 나열: "…타당성(과학기술기본계획·국가첨단전략산업 육성계획 등…)"
_PAREN_RE = re.compile(r"\(([^)]{4,160})\)")

# 항목으로 보기엔 너무 일반적인 말 — 이것까지 검사하면 전 절이 경고투성이가 된다.
_STOPWORDS = frozenset(
    {"등", "및", "각", "해당", "관련", "주요", "기타", "분석", "검토", "평가", "서술", "제시"}
)
_MIN_TERM_CHARS = 3
_MAX_TERM_CHARS = 20

# 본문이 그 항목을 다뤘다고 볼 겹침 — 표면 일치(1.0)와 표기 흔들림("주민수용성" vs
# "주민 수용성", "지역사회 수용성")을 함께 받되, 무관한 문장이 우연히 걸리지 않을 선.
COVERED_THRESHOLD = 0.7


def _clean(raw: str) -> str:
    term = _NUMBER_PREFIX_RE.sub("", raw.strip()).strip("()[]\"'“”«» ")
    # 괄호가 한쪽만 남으면 잘린 항목이다("교차전략(SO/ST/WO/WT") - 여는 괄호 앞까지만 쓴다.
    if term.count("(") != term.count(")"):
        term = term.split("(")[0].strip()
    term = re.sub(r"\s*(등|및)\s*$", "", term).strip()
    return term


def coverage_terms(direction: str, key_points: list[str] | None) -> list[str]:
    """목차 지시문 → 검사할 항목 목록.

    key_points가 이미 체크리스트다("국정과제 연계", "글로벌 경쟁 현황"). 항목 안에
    다시 나열이 있으면(위험요인 - 인허가·환경·법령) 쪼개고, 방향 지시문의 괄호 나열도
    항목으로 받는다. LLM을 쓰지 않는다 — 매번 같은 목록이 나와야 경고를 믿을 수 있다.
    """
    out: list[str] = []
    for point in key_points or []:
        body = _LABEL_SPLIT_RE.split(point.strip(), maxsplit=1)
        target = body[-1] if len(body) > 1 else body[0]
        parts = [_clean(p) for p in _SPLIT_RE.split(target)]
        # 나열이 아니면(한 덩어리) 그 자체가 항목이다.
        out.extend(parts if len(parts) > 1 else [_clean(target)])
    for m in _PAREN_RE.finditer(direction or ""):
        inner = m.group(1)
        if not _SPLIT_RE.search(inner):
            continue  # 나열이 아닌 괄호(부연 설명)는 항목이 아니다
        out.extend(_clean(p) for p in _SPLIT_RE.split(inner))

    seen: set[str] = set()
    terms: list[str] = []
    for t in out:
        if not (_MIN_TERM_CHARS <= len(t) <= _MAX_TERM_CHARS) or t in _STOPWORDS or t in seen:
            continue
        seen.add(t)
        terms.append(t)
    return terms


def covered(term: str, text: str) -> bool:
    """본문(또는 근거 풀)이 이 항목을 다뤘는가.

    표면 일치를 먼저 보고, 아니면 어휘 겹침으로 표기 흔들림을 흡수한다 — 근거 대목
    정렬(services/qa/alignment)과 같은 잣대를 써서 화면의 판정끼리 어긋나지 않게 한다.
    """
    if not term or not text:
        return False
    if term.replace(" ", "") in text.replace(" ", ""):
        return True
    score = overlap_score(term, text)
    # NO_SCORE(잴 수가 없음)를 미달로 읽으면 "안 다뤘다"는 경고가 헛돈다 - 표면 일치가
    # 위에서 이미 실패했으므로 여기서는 보수적으로 '커버 안 됨'을 유지하되, 판정 근거가
    # 겹침이 아니라 **측정 불가**였다는 것을 구분해 둔다(0.0과 -1은 다른 사실이다).
    if score == NO_SCORE:
        return False
    return score >= COVERED_THRESHOLD


def findings_for_section(
    chapter_number: int,
    section_number: int,
    content: str,
    direction: str,
    key_points: list[str] | None,
    pool_text: str,
    verdict: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """미반영 항목을 원인별로 갈라 경고 행으로 만든다 (순수 함수 — 테스트 대상)."""
    terms = coverage_terms(direction, key_points)
    if not terms:
        return []
    # 판정을 받았으면 그걸 따른다 - 어휘 겹침은 표기 흔들림만 흡수할 뿐 내용 유무를
    # 못 가른다(실측 정밀도 25%·재현율 33%). 판정이 없으면(모델 실패) 어휘로 폴백.
    verdict = verdict or {}
    missing = [t for t in terms if not verdict.get(t, covered(t, content))]
    if not missing:
        return []

    ref = f"{chapter_number}.{section_number}"
    in_pool = [t for t in missing if covered(t, pool_text)]
    no_pool = [t for t in missing if t not in in_pool]

    out: list[dict[str, Any]] = []
    if in_pool:
        out.append(
            {
                "chapter_number": chapter_number,
                "severity": "warning",
                "category": "목차 지시 미반영",
                "section_ref": ref,
                "detail": (
                    f"목차가 요구한 {len(in_pool)}건이 본문에 없습니다: {' · '.join(in_pool)}"
                    " (근거 자료는 있음) - 이 항목을 포함해 절을 다시 쓰면 채워집니다."
                ),
            }
        )
    if no_pool:
        # 근거 풀에도 없는 항목은 두 가지가 섞인다 - 자료가 실제로 없는 내용 항목
        # (TAM/SAM/SOM)과, 애초에 자료로 찾을 게 아닌 작성·서식 지시(검색식 명시,
        # 과제고유번호). 코드로는 못 가르므로 단정하지 않고 둘 다 알린다. 여기서
        # "자료를 찾으세요"라고 단정하면 후자에는 틀린 권고가 된다(2026-08-11 실측:
        # 자료 없음 8건 중 3건이 서식 지시였다).
        out.append(
            {
                "chapter_number": chapter_number,
                "severity": "warning",
                "category": "목차 지시 미반영",
                "section_ref": ref,
                "detail": (
                    f"목차가 요구한 {len(no_pool)}건이 본문에 없고 근거 자료에도 해당 내용이"
                    f" 없습니다: {' · '.join(no_pool)} - 내용 항목이면 자료를 먼저 찾으세요"
                    "(그냥 다시 쓰면 근거 없이 서술하게 됩니다). 서술 방식 지시면 재작성으로"
                    " 채워집니다."
                ),
            }
        )
    return out


# ── 이행 판정을 LLM으로 ─────────────────────────────────────────────────────
# 어휘 겹침으로 '다뤘는가'를 재던 방식은 **실측에서 신뢰할 수 없었다**(2026-08-12,
# 목차 지시 항목 37개): 경고 정밀도 25% · 재현율 33%. 사용자에게 뜬 경고 4건 중
# 진짜는 1건이었다. 원인은 교차언어 근거 대조와 같은 부류다 - 어휘 겹침은 "단어가
# 있는가"를 재지 "내용이 있는가"를 못 잰다.
#   '리스크'      → 본문엔 "위험 요인"으로 서술 → 미반영으로 오판(거짓 경고)
#   '시간적 범위' → 문구만 있고 내용 없음      → 다뤘다고 오판(놓침)
# 항목이 절당 2~3개뿐이라 전부 판정에 넘겨도 절당 1콜이다.

_JUDGE_SYSTEM = (
    "너는 보고서 검토자다. 목차가 지시한 항목을 본문이 실제로 다뤘는지 판정하라.\n"
    "- '다뤘다'는 그 항목에 대해 내용이 서술된 것이다. 단어만 스쳐 지나가면 안 다룬 것이다.\n"
    "- 표현이 달라도 내용이 있으면 다룬 것이다('주민 수용성' = '지역사회 수용도').\n"
    '항목마다 {"term": "...", "covered": true/false} 로 JSON 배열만 출력하라.'
)
_JUDGE_MAX_TOKENS = 1500
_JUDGE_BODY_CHARS = 14000


async def judge_covered(
    terms: list[str],
    content: str,
    *,
    section_ref: str,
    model: str,
    user_id: Any = None,
    project_id: Any = None,
    client: Any = None,
) -> dict[str, bool]:
    """항목별 이행 여부를 LLM에 묻는다. 실패하면 빈 dict — 호출부가 어휘 판정으로 폴백."""
    import json

    from src.clients.llm.base import CompletionRequest, Message
    from src.clients.llm.factory import get_llm_client
    from src.clients.llm.token_tracker import token_context

    if not terms or not content.strip():
        return {}
    prompt = (
        f"[절] {section_ref}\n[목차가 지시한 항목] {', '.join(terms)}\n"
        f"[본문]\n{content[:_JUDGE_BODY_CHARS]}"
    )
    try:
        with token_context(user_id=user_id, project_id=project_id, operation="qa.coverage_judge"):
            response = await (client or get_llm_client()).complete(
                CompletionRequest(
                    model=model,
                    system=_JUDGE_SYSTEM,
                    messages=[Message(role="user", content=prompt)],
                    max_tokens=_JUDGE_MAX_TOKENS,
                    cache_key=None,
                )
            )
        raw = response.content.strip()
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            return {}
        items = json.loads(raw[start : end + 1])
    except Exception:
        logger.warning("design_coverage.judge_failed", section_ref=section_ref, exc_info=True)
        return {}
    return {
        str(i["term"]): bool(i.get("covered")) for i in items if isinstance(i, dict) and "term" in i
    }
