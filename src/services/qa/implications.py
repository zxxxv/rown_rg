"""시사점·제언 절의 두 규칙 — 결정적 검사.

규칙은 2026-08-27에 예타 보고서로 종결부를 두 벌 써 보며 세웠다. 프롬프트에만
두면 모델·언어가 바뀔 때 같이 흔들리므로(이 저장소의 기존 규약: 프롬프트 규칙은
코드 필터가 최종 관문이다) 여기서 결정적으로 잰다.

① 시사점 절은 새 수치를 도입하지 않는다
   시사점은 앞 절에서 확인된 것을 해석하는 자리다. 여기서 처음 나오는 수치는
   근거 없이 단정한 것이거나, 앞 절과 어긋난 값이다. 실측(v6 2.5절)에서 시사점
   절이 세계은행 노출지수·EU 이행법 일정을 **여기서 처음** 끌어왔다.

   비교는 문자열이 아니라 **크기**로 한다. '492.5억'과 '492억 5,000만 원'은 같은
   값인데 문자열로 재면 창작으로 잡힌다(2026-08-27 골든에서 실제로 오탐했다).
   gate.korean_magnitude가 이미 그 환산을 갖고 있어 그대로 쓴다.

② 주체를 붙일 수 없는 문장은 제언이 아니다
   "~해야 한다"는데 누가 하는지 없으면 실행할 수 없는 문장이다. 한국어는 주어를
   자주 생략하므로 **당위 문장에 한해서만** 본다.
"""

from __future__ import annotations

import re

from src.services.qa.gate import normalize_number, significant_numbers
from src.services.qa.pm_verify import _magnitude

# 시사점 성격의 절 — insights의 자동 선택과 같은 어휘를 쓴다(두 곳이 어긋나면
# 화면이 요약한 절과 검사한 절이 달라진다).
IMPLICATION_TITLE_RE = re.compile(r"시사점|제언|결론|소결|Next\s*Step", re.IGNORECASE)

# 당위 문형 — "무엇을 해야 한다"는 주장. 좁게 잡는다.
#
# 실측(2026-08-27, 보고서 3종)에서 "필요가 있음"·"요구됨"까지 넣었더니 절당 4~9건이
# 나왔고 그중 다수가 제언이 아니었다: 사실 서술("업종별로 인지 수준이 갈려"), 부정형
# ("동일 하드웨어로 실행할 필요가 없으며"), 잘린 문장 조각. 한국어에서 이 꼴들은
# 주어 생략이 관용적이라 주체 유무로 제언을 가릴 수 없다.
# "~해야 한다/함" 계열만 남긴다 — 이건 행위 요구가 분명하고 주체가 빠지면 실제로
# 실행할 수 없는 문장이다.
_OBLIGATION_RE = re.compile(r"(해야\s*(한다|한다는|함|하며|하고|할)|하여야|되어야\s*(한다|함))")

# 부정형은 당위가 아니다 — "~할 필요가 없다"는 하지 말라는 뜻이다.
_NEGATION_RE = re.compile(r"필요가?\s*없|하지\s*않아도|아니어야|않아야")

# 주체 어휘 — 문장 안에 이 중 하나가 있으면 누가 하는지 정해진 것으로 본다.
_ACTOR_RE = re.compile(
    r"정부|부처|주관부처|중앙부처|지자체|지방자치단체|기획재정부|과학기술정보통신부|"
    r"산업통상자원부|국토교통부|환경부|중소벤처기업부|위원회|청(?:은|이|에서)|"
    r"기관|수행기관|참여기관|전담기관|연구기관|사업자|기업|협회|단체|조합|"
    r"발주처|주관사|컨소시엄|사업단|추진단|공단|공사|재단|진흥원|연구원|"
    # 국가·경제권도 주체다 — "EU는 …해야 한다"를 주체 없음으로 잡던 오탐(2026-08-27).
    r"EU|유럽연합|미국|중국|일본|한국|우리나라|정부부처|국회|위원회"
)

# 한 문장으로 자를 경계 — 개조식이라 줄바꿈이 문장 경계 역할을 겸한다.
_SENT_SPLIT = re.compile(r"[.\n]|(?<=음)\s|(?<=함)\s")

# 수치 추출 전에 걷어낼 것들 — 여기 있는 숫자는 사실 주장이 아니다.
# 실측(2026-08-27)에서 오탐 3건이 전부 이 계급이었다: 헤딩의 절 번호("## 3.7"),
# 출처 번호("(출처 35, 51 …)"), 인용 대괄호.
_NOISE_RE = re.compile(
    r"\(출처[^)]*\)"  # 출처 표기
    r"|\[\d+(?:\s*,\s*\d+)*\]"  # 대괄호 인용
)


def _strip_noise(text: str) -> str:
    """수치 주장이 아닌 숫자를 걷어낸다 — 헤딩의 절 번호, 출처 표기, 대괄호 인용."""
    body = "\n".join(line for line in (text or "").split("\n") if not line.lstrip().startswith("#"))
    return _NOISE_RE.sub(" ", body)


# 크기 비교 허용 오차(상대) — 반올림 표기('약 1조 96억'↔'1조 96억')를 같은 값으로.
_REL_TOL = 1e-9


# 숫자와 한국어 자리 단위를 통째로 잡는다 - '492억 5,000만'처럼 이어진 합성 표기까지.
# significant_numbers는 단위를 떼고 숫자만 주기 때문에(492.5 / 492 + 5,000) 그것만으로는
# '492.5억'과 '492억 5,000만 원'이 같은 값임을 알 수 없다(2026-08-27 유닛에서 잡힘).
_VALUE_SPAN = re.compile(
    r"\d[\d,]*(?:\.\d+)?(?:\s*[조억만천])?(?:\s*\d[\d,]*(?:\.\d+)?\s*[조억만천])*"
    # %까지 함께 보고한다 - 사람이 본문에서 찾을 때 '31.6'보다 '31.6%'가 빠르다.
    r"(?:\s*%)?"
)


def _span_value(span: str) -> float | None:
    """표기 → 크기. pm_verify의 한국어 자리 환산을 그대로 쓴다(두 곳이 어긋나면 안 된다)."""
    value = _magnitude(span)
    if value is not None:
        return value
    try:
        return float(normalize_number(span))
    except ValueError:
        return None


def _magnitudes(text: str) -> set[float]:
    """본문에 실재하는 값의 크기 집합."""
    out: set[float] = set()
    for m in _VALUE_SPAN.finditer(_strip_noise(text)):
        value = _span_value(m.group())
        if value is not None:
            out.add(value)
    return out


def _has_close(value: float, pool: set[float]) -> bool:
    for other in pool:
        if abs(value - other) <= max(abs(value), abs(other)) * _REL_TOL:
            return True
    return False


def new_numbers(content: str, prior_text: str) -> list[str]:
    """앞 절 어디에도 없는 수치 — 시사점 절이 새로 끌어온 값.

    보고 대상은 significant_numbers가 '사실 주장'으로 인정한 토큰으로 좁힌다
    (연도·절 번호·한 자리 수는 그쪽이 이미 걸러 준다). 비교는 그 토큰이 속한
    합성 표기의 크기로 한다.
    """
    if not content:
        return []
    pool = _magnitudes(prior_text)
    body = _strip_noise(content)
    # %·단위가 붙은 채로 오므로 정규화해 맞춘다('31.6%' -> '31.6').
    claimable = {normalize_number(t) for t in significant_numbers(body)}
    out: list[str] = []
    seen: set[str] = set()
    for m in _VALUE_SPAN.finditer(body):
        span = m.group().strip()
        head_token = re.match(r"\d[\d,]*(?:\.\d+)?", span)
        if head_token is None or normalize_number(head_token.group()) not in claimable:
            continue
        value = _span_value(span)
        if value is None or _has_close(value, pool):
            continue
        if span in seen:
            continue
        seen.add(span)
        out.append(span)
    return out


def actorless_recommendations(content: str) -> list[str]:
    """주체 없는 당위 문장 — 제언인데 누가 할 일인지 없는 것."""
    if not content:
        return []
    out: list[str] = []
    for raw in _SENT_SPLIT.split(content):
        sent = (raw or "").strip()
        if len(sent) < 12 or not _OBLIGATION_RE.search(sent):
            continue
        if _NEGATION_RE.search(sent) or _ACTOR_RE.search(sent):
            continue
        out.append(sent)
    return out


def implication_findings(sections: list[tuple[object, str]]) -> list[dict[str, object]]:
    """(절 계획, 본문) → 시사점 절 두 규칙 경고 행(pm_verify 행 모양).

    시사점 성격의 절에만 건다. 순서는 안에서 목차 순으로 맞춘다 — 앞 절이 무엇인지가
    규칙 ①의 전부라 호출부 정렬에 기댈 수 없다.

    둘 다 warning이다. 값 누락(metric_values)과 달리 이 둘은 판정에 회색지대가
    있다: 한국어는 주어를 자주 생략하고, 시사점 절이 배경 수치 하나를 되풀이하는
    것이 늘 결함은 아니다. 사람이 보고 고르라는 신호로 둔다.
    """
    rows: list[dict[str, object]] = []
    prior: list[str] = []
    # 호출부의 정렬을 믿지 않는다 - 규칙 ①은 "앞 절에 있었나"가 전부라, 순서가
    # 뒤집히면 판정이 통째로 뒤집힌다(pm_verify의 pairs는 장별 묶음이라 장 안의
    # 절 순서가 보장되지 않는다).
    ordered = sorted(
        sections,
        key=lambda x: (getattr(x[0], "chapter_number", 0), getattr(x[0], "section_number", 0)),
    )
    for plan, content in ordered:
        if not IMPLICATION_TITLE_RE.search(getattr(plan, "title", "") or ""):
            prior.append(content)
            continue
        ref = f"{plan.chapter_number}.{plan.section_number}"
        fresh = new_numbers(content, "\n".join(prior))
        if fresh:
            rows.append(
                {
                    "chapter_number": plan.chapter_number,
                    "severity": "warning",
                    "category": "시사점 새 수치",
                    "section_ref": ref,
                    "detail": (
                        f"앞 절에 없는 수치 {len(fresh)}건이 시사점 절에서 처음 등장: "
                        + ", ".join(fresh[:8])
                        + " — 시사점은 앞 절에서 확인된 것을 해석하는 자리다. "
                        "근거가 앞 절에 있으면 그 값을 그대로 옮기고, 없으면 그 대목을 빼라"
                    ),
                }
            )
        actorless = actorless_recommendations(content)
        if actorless:
            rows.append(
                {
                    "chapter_number": plan.chapter_number,
                    "severity": "warning",
                    "category": "주체 없는 제언",
                    "section_ref": ref,
                    "detail": (
                        f"누가 할 일인지 없는 당위 문장 {len(actorless)}건: "
                        + " / ".join(s[:45] for s in actorless[:4])
                        + " — 주체(정부·주관부처·수행기관·기업 등)를 붙일 수 없으면 제언이 아니다"
                    ),
                }
            )
        prior.append(content)
    return rows
