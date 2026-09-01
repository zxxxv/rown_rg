"""서사 사슬 — 장 내 순차 작성에서 앞 절이 '다룬 것'을 다음 절에 넘긴다(실험 C).

병렬 작성의 구조적 맹점은 작성기가 다른 절이 뭘 썼는지 모른다는 것이다(4차 실측:
절 간 중복 463문장). 토픽 소유권(계획 시점)이 장 간 재서술을 맡고, 이 모듈은 장 안을
맡는다 — 장 안만 순차로 세우고(장 간은 병렬 유지: 세마포어 4·장 4개면 벽시계가 평면
병렬과 같다) 완료된 앞 절의 요약을 구조화 JSON으로 다음 절에 준다. "1.1~1.3을 모아
1.4에서 결론을 도출"하는 전개가 이 경로로 성립한다.

오염 채널 차단 2겹(서술 요약 전달이 무근거 +39%였던 옛 실측의 재발 방지):
1. 요약 자체에 수치·통계·연도를 싣지 않는다 — RAPTOR 요약 규칙 승계. 상용 모델
   재실측(2026-08-11)에서 이 규칙의 요약은 무해가 실증됐다.
2. 주입 프레이밍이 "요약은 근거가 아니다 — 인용 금지, 재서술 금지, 접속·중복 회피·
   논지 연결에만 쓰라"를 명시한다.

가드레일(사전 등록): 무근거/천자·근거 불일치가 기준 런 대비 늘면 기각.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import create_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.config import settings

logger = structlog.get_logger(__name__)

# 요약 상한 — 사슬은 장 안에서 절 수만큼 누적되므로 짧아야 한다(절당 3~5문장).
MAX_SUMMARY_TOKENS = 700
MAX_TOPICS = 8
MAX_SUMMARY_CHARS = 600
MAX_TOPIC_CHARS = 40

_SUMMARY_SYSTEM = (
    "너는 보고서 절을 압축하는 요약가다. 주어진 절 본문이 무엇을 다뤘는지 한국어로"
    " 요약하라. 규칙:\n"
    "- 구체적인 수치·통계·연도 값은 싣지 말고 '급증', '과반' 같은 정성 표현으로 바꿔라.\n"
    "- 새로운 주장을 만들지 마라. 본문에 없는 내용 금지.\n"
    "- 요약은 3~5문장. topics는 이 절이 다룬 소주제를 명사구로(중복 서술 방지용 목록).\n"
    '- 아래 JSON만 출력하라(설명 없이): {"summary":"...","topics":["...","..."]}'
)


def _parse_summary(text: str) -> tuple[str, list[str]] | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m is None:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = " ".join(str(data.get("summary") or "").split())[:MAX_SUMMARY_CHARS]
    topics = [
        " ".join(str(t).split())[:MAX_TOPIC_CHARS]
        for t in (data.get("topics") or [])
        if isinstance(t, str) and t.strip()
    ][:MAX_TOPICS]
    if not summary:
        return None
    return summary, topics


async def summarize_section(
    *,
    label: str,
    title: str,
    content: str,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
    client: LLMClient | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """완료된 절 1건 → 사슬 엔트리. 어떤 실패든 None — 요약이 작성을 막으면 안 된다."""
    try:
        llm = client or create_llm_client()
        request = CompletionRequest(
            model=model or settings.raptor_model,
            system=_SUMMARY_SYSTEM,
            messages=[Message(role="user", content=f"[{label} {title}]\n\n{content}")],
            max_tokens=MAX_SUMMARY_TOKENS,
            temperature=0.1,
        )
        with token_context(user_id=user_id, project_id=project_id, operation="narrative.summary"):
            response = await llm.complete(request)
        parsed = _parse_summary(response.content)
        if parsed is None:
            logger.warning("narrative_chain.parse_failed", section=label)
            return None
        summary, topics = parsed
        return {"section": label, "title": title, "summary": summary, "topics": topics}
    except Exception:
        logger.warning("narrative_chain.summarize_failed", section=label, exc_info=True)
        return None


def format_chain_injection(prior: list[dict[str, Any]]) -> str:
    """같은 장에서 이미 완성된 앞 절들의 요약 → 작성 guidance 블록.

    JSON을 그대로 싣는다(사용자 결정 2026-08-20: 구조를 확실히) — 서술문으로 풀면
    작성기가 요약 문장을 근거처럼 흡수한다.
    """
    if not prior:
        return ""
    payload = json.dumps(
        [
            {
                "절": e.get("section", ""),
                "제목": e.get("title", ""),
                "요약": e.get("summary", ""),
                "다룬 토픽": e.get("topics", []),
            }
            for e in prior
        ],
        ensure_ascii=False,
    )
    return (
        "이 장에서 이미 완성된 앞 절들의 요약(JSON):\n"
        f"{payload}\n"
        "규칙: 이 요약은 근거 자료가 아니다 — 인용하지 말고, 요약 속 내용을 다시 서술하지"
        ' 마라. 용도는 세 가지뿐이다: ①앞 절이 이미 다룬 토픽은 건너뛰거나 "(앞 절 참조)"'
        " 한 문장으로 접속한다 ②앞 절의 전개를 이어받아 이 절의 고유한 몫을 쓴다 ③이 절이"
        " 장의 결론·시사점이라면 앞 절들의 흐름을 종합하되, 수치는 반드시 검색 근거와 '앞"
        " 절에서 확정된 값' 주입에서만 가져와 (출처 n)으로 인용한다."
    )
