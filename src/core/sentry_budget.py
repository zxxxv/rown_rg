"""런당 Sentry 이벤트 상한 — 대량 절·자료를 다루는 경로가 quota를 태우지 않게.

이 앱의 백그라운드 실패는 **절 단위·자료 단위**로 난다. 35절 보고서에서 LLM이
띄엄띄엄 죽거나 13건 업로드가 통째로 실패하면 한 번의 런이 수십 건의 이벤트를
올린다. 신호는 "이 런에서 이런 실패가 났다" 몇 건이면 충분하고, 나머지 상세는
구조화 로그(logger.warning/exception)에 전부 남는다 — 여기서 줄이는 건 **보고**
뿐이고 폴백·격리 같은 실제 동작은 호출부가 그대로 한다.

예산은 contextvar에 담는다. 런은 asyncio 태스크로 돌고(workflows.runner._execute),
자식 태스크는 생성 시점의 컨텍스트를 복사하므로 한 런의 절 태스크들은 같은 dict를
공유하고 **동시에 도는 다른 런과는 섞이지 않는다**. 런 밖(요청에서 spawn된 업로드
색인 등)은 프로세스 공용 예산을 쓴다.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import sentry_sdk

# 한 런에서 같은 종류(kind)의 실패를 몇 건까지 올릴지. 초과분은 로그만 남는다.
DEFAULT_RUN_CAPTURE_LIMIT = 5

# 런 밖에서 도는 백그라운드(업로드 색인 등)가 쓰는 공용 예산. 런 시작마다 새 dict로
# 갈아끼우므로 런 안의 카운트가 여기 섞이지 않는다.
_PROCESS_BUDGET: dict[str, int] = {}
_budget: ContextVar[dict[str, int]] = ContextVar("sentry_capture_budget", default=_PROCESS_BUDGET)


def reset_capture_budgets() -> None:
    """이 런의 예산을 새로 연다 — 런 진입부(workflows.runner._execute)에서 호출.

    현재 태스크의 컨텍스트에만 새 dict를 꽂는다. 이 뒤에 만들어지는 자식 태스크
    (절 작성, 자료 색인)는 그 dict를 물려받아 같은 예산을 쓰고, 형제 런은 자기
    dict를 따로 갖는다.
    """
    _budget.set({})


def capture_budgeted(
    kind: str,
    exc: BaseException | None = None,
    *,
    limit: int = DEFAULT_RUN_CAPTURE_LIMIT,
    tags: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
) -> bool:
    """예산이 남아 있을 때만 capture한다. 실제로 올렸으면 True.

    ``exc``가 None이면 sys.exc_info()를 쓴다(이름 없는 ``except Exception:`` 용).
    상한에 닿는 마지막 1건에는 "이후는 로그만"을 extra로 붙여, Sentry에서 이벤트
    수를 실제 발생 건수로 오해하지 않게 한다.

    호출부의 흐름은 바꾸지 않는다 — 반환값을 무시해도 되고, False가 와도 폴백·
    로깅은 이미 호출부가 끝낸 상태다.
    """
    counts = _budget.get()
    seen = counts.get(kind, 0) + 1
    counts[kind] = seen
    if seen > limit:
        return False

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("bg_failure", kind)
        for key, value in (tags or {}).items():
            scope.set_tag(key, value)
        for key, value in (extras or {}).items():
            scope.set_extra(key, value)
        scope.set_extra("occurrence", f"{seen}/{limit}")
        if seen == limit:
            scope.set_extra(
                "capture_budget",
                f"limit reached ({limit}); further '{kind}' failures in this run are logged only",
            )
        sentry_sdk.capture_exception(exc)
    return True
