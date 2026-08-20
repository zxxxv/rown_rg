"""작성 콜 추론(thinking) 예산 정책.

thinking이 기본 켜지는 모델(Opus 5)은 요청에 아무 설정이 없으면 effort 기본값
(high)으로 추론을 돌고, 그 추론 토큰이 전부 출력으로 과금된다. 절 작성은 근거
청크가 통째로 프롬프트에 들어있고 파트 계획도 별도 콜이 끝낸 뒤라 추론 집약형이
아닌데, 고급 런 실측(2026-08-14, $33.84 분해)에서 작성 출력 661k 토큰 중 본문은
~260k — 절반 이상이 추론 과금이었다. 같은 주제 표준 런(Sonnet 4.6, thinking 없음)과
품질 차이는 근소해, 작성 콜만 effort를 낮춘다(설정 write_effort, 기본 low).

적용 범위는 작성(후보 생성·분할 파트·블록 재작성)뿐이다 — 파트 소주제 계획과
pm_verify는 콜 수가 적어 절감 실익이 없고 판정·구조 품질을 지키는 쪽이 이득이라
기본 추론을 유지한다. thinking을 끄는(disabled) 선택지는 쓰지 않는다 — Opus 5는
끄면 <thinking> 태그가 본문에 새는 실패 모드가 문서화돼 있어 보고서에 치명적.
"""

from src.clients.llm.models import thinking_default_on
from src.core import app_settings


def write_effort(model: str, *, synthesis: bool = False) -> str | None:
    """작성 콜에 적용할 effort. thinking 기본-on 모델이 아니면 None(파라미터 생략).

    Sonnet 4.6처럼 thinking이 원래 꺼진 채 도는 모델에 effort를 보내면 절감은
    없이 생성 동작만 바뀔 수 있어(기본 high → low) 건드리지 않는다 — 표준 모드의
    실측 품질 기준선을 흔들지 않기 위한 가드다.

    synthesis=True는 종합 절(builds_on 보유 — 앞 절들의 값·요약을 받아 결론을 내는
    임무)로, 별도 설정(write_effort_synthesis, 기본 medium)을 쓴다. 8/14 low A/B는
    일반 절 기준이라 종합 절의 통찰 깊이는 미검증(2026-08-20 사용자 가설) — 정독의
    "뻔한 단계론" 일반론이 종합 절에 몰린 것과 정합해 절 범위 한정으로 상향한다.
    """
    if not thinking_default_on(model):
        return None
    if synthesis:
        return app_settings.get_str("write_effort_synthesis") or None
    return app_settings.get_str("write_effort") or None
