"""계산 지표에 값이 없는 절 — 결정적 검사.

2026-08-27 실측에서 나온 구멍이다. AI반도체 예타 보고서(35절 377,865자)의 6.3
경제적 타당성 절은 `B/C`를 18번 말하는데 **계산된 값이 한 번도 없다**. 전부
"B/C ≥ 1.000이면 타당으로 판정한다", "세 지표를 병렬 산출한다" 같은 방법·기준
서술뿐이다. 보고서 전체를 훑어도 B/C·NPV·IRR에 값이 붙은 자리는 0건이었다.
예타에서 B/C는 결론 그 자체라, 심사자가 그 절을 읽고 판단할 수가 없다.

원인은 게으름이 아니라 규칙 충돌이었다. 작성 규칙은 문장마다 (출처 N)을 요구하는데
**계산 결과에는 붙일 출처가 없다** — 총사업비와 총편익을 나눈 값은 어떤 자료에도
안 적혀 있다. 그래서 모델이 값을 쓸 수 있는 합법적인 길이 없었다.
규칙 쪽은 agent_source_rules에 파생 수치 조항을 더해 길을 냈고, 이 검사는 그래도
값이 안 나온 절을 표면화한다.

어휘가 아니라 **값**을 본다: 지표 이름은 있는데 그 지표에 붙은 수치가 하나도 없을
때만 잡는다. 키포인트 검사(keypoints)는 어휘 겹침으로 재기 때문에 이 구멍을 통과
시킨다 — "B/C"라는 말이 18번 나오면 반영된 것으로 읽는다.
"""

from __future__ import annotations

import re

# 계산해서 내놓아야 하는 지표 — 이름만 말하고 값을 안 주면 결론이 빠진 것이다.
# 좁게 시작한다: 예타·타당성 문서의 판정 지표. 늘릴 때는 "값이 없으면 결론이
# 없는 것과 같은가"를 기준으로 판단한다(단순 언급이 흔한 말은 넣지 않는다).
METRICS: tuple[str, ...] = (
    "B/C",
    "NPV",
    "IRR",
    "비용편익비율",
    "순현재가치",
    "내부수익률",
)

# 지표 뒤 이만큼 안에 숫자가 있으면 "값이 붙었다"로 본다.
_WINDOW = 25

# 기준·조건 서술은 값이 아니다 — "B/C ≥ 1.000이면 타당"의 1.000은 판정 문턱이지
# 이 사업의 계산 결과가 아니다. 이 문맥이 곁에 있으면 값으로 세지 않는다.
_THRESHOLD_CTX = re.compile(r"≥|≤|이상|이하|초과|미만|상회|하회|기준|조건|충족|판정|경우")

_NUM = re.compile(r"[0-9][0-9,.]*")

# 인용 표식은 값이 아니다 — "B/C 하락 폭이 큰 축에 해당함(출처 1)"의 1을 값으로 읽으면
# 값 없는 절이 통과한다(2026-08-27 실측: 예타 6.3의 B/C가 이렇게 빠져나갔다).
_CITATION = re.compile(r"\(출처[^)]*\)|\[\d+(?:\s*,\s*\d+)*\]")


def _metric_pattern(metric: str) -> re.Pattern[str]:
    return re.compile(re.escape(metric))


def metric_value_hits(content: str, metric: str) -> int:
    """이 절에서 그 지표에 **값이 붙은** 자리 수.

    문턱 판정은 지표 **뒤**만 본다. 앞까지 넓게 보면 "B/C ≥ 1.000 기준이며, 산출
    결과 B/C 1.23이다"에서 뒤의 진짜 값이 앞 문장의 '기준' 때문에 죽는다
    (2026-08-27 유닛에서 잡힘). 지표와 수 사이, 그리고 수 바로 뒤만 살핀다.
    """
    content = _CITATION.sub(" ", content)
    hits = 0
    for m in _metric_pattern(metric).finditer(content):
        tail = content[m.end() : m.end() + _WINDOW]
        num = _NUM.search(tail)
        if num is None:
            continue
        # "B/C ≥ 1.000"은 문턱, "B/C가 1.0을 상회하면"도 문턱이다.
        between = tail[: num.start()]
        after = tail[num.end() : num.end() + 12]
        if _THRESHOLD_CTX.search(between) or _THRESHOLD_CTX.search(after):
            continue
        hits += 1
    return hits


def missing_metric_values(content: str) -> list[str]:
    """언급은 되는데 값이 없는 지표 목록."""
    if not content:
        return []
    stripped = _CITATION.sub(" ", content)
    out: list[str] = []
    for metric in METRICS:
        if not _metric_pattern(metric).search(stripped):
            continue
        if metric_value_hits(content, metric) == 0:
            out.append(metric)
    return out


def metric_findings(sections: list[tuple[object, str]]) -> list[dict[str, object]]:
    """(절 계획, 본문) → 값 없는 계산 지표 경고 행(pm_verify 행 모양).

    critical인 이유: 타당성 판정 지표에 값이 없으면 그 절은 결론이 없는 절이다.
    형식 결함(warning)과 달리 사람이 반드시 손대야 한다.
    """
    rows: list[dict[str, object]] = []
    for plan, content in sections:
        missing = missing_metric_values(content)
        if not missing:
            continue
        rows.append(
            {
                "chapter_number": plan.chapter_number,
                "severity": "critical",
                "category": "수치 산출 누락",
                "section_ref": f"{plan.chapter_number}.{plan.section_number}",
                "detail": (
                    f"{', '.join(missing)}을(를) 말하면서 계산된 값이 하나도 없음 — "
                    "판정 기준·산식만 있고 이 사업의 결과값이 빠졌다. "
                    "입력값(총사업비·총편익 등)이 앞 절에 있으면 산식과 함께 값을 적고, "
                    "입력값이 없으면 그 자료부터 보강해야 한다"
                ),
            }
        )
    return rows
