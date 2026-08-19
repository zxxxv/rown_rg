"""AI 실행 계획 — 설계 브리프의 LLM 층(목차당 1콜).

결정적 브리프(design_brief)는 '무엇으로 검색되는가'를 계산해 보여준다. 그러나 사용자가
비싼 런을 승인하기 전에 알고 싶은 것은 그 너머다 — 각 절이 **무슨 목표**를 갖고,
**어떤 자료를 어떻게 찾아**, **어떻게 써 나갈 것인지**. 이건 의미 판단이라 계산이
안 되고, 이 모듈이 그 판단을 LLM 1콜로 만들어 브리프에 얹는다.

원칙:
- 셀 수 있는 것(질의 문자열·중복·분량·비용)은 이 모듈 밖(결정적)이다. LLM 판정이
  그것까지 대신하면 틀린 안심을 만든다(pm_verify 정밀도 57% 전례).
- 실패는 None — 게이트는 결정적 내용만으로 뜬다. LLM이 게이트를 막으면 안 된다.
- 산출은 '제안'이다. 목차에 자동 반영하지 않는다(목차는 사람이 만든다, 2026-08-03).
  승인된 계획은 runner가 config["_design_plan"]으로 커밋해 작성 프롬프트에 실린다 —
  계획이 안내문이 아니라 계약이 되는 지점.
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

logger = structlog.get_logger(__name__)

# 출력 상한은 절 수에 비례해야 한다 — 고정 6000은 20절 목차에서 JSON을 중간에 끊어
# 통파싱 실패를 만들었다(2026-08-14 첫 실전 브리프: 토큰 14,013 소모 후 ai_plan=None).
# 절당 3문장(한국어 ≈ 400~600토큰) + 장 목표·흐름 몫. 상한 캡은 폭주 방지.
_BASE_TOKENS = 2000
_TOKENS_PER_SECTION = 700
_MAX_TOKENS_CAP = 16000


def _max_tokens(n_sections: int) -> int:
    return min(_MAX_TOKENS_CAP, _BASE_TOKENS + _TOKENS_PER_SECTION * max(1, n_sections))


SYSTEM_PROMPT = """너는 정부·공공 보고서의 실행 계획을 세우는 설계 책임자다.
입력으로 보고서 주제와 이미 확정된 목차(장·절·작성 방향·핵심 포인트·검색 질의)가 JSON으로 주어진다.
목차 자체를 바꾸자고 제안하지 마라 — 목차는 확정이고, 너는 그것을 어떻게 실행할지만 계획한다.

각 절에 대해:
1. goal — 이 절이 보고서 전체 목적 안에서 무엇을 밝혀야 하는가 (1문장)
2. source_strategy — 어떤 기관·자료 유형을 어떤 각도로 찾을 것인가
   (1~2문장, 기관·자료 유형을 구체적으로)
3. writing_plan — 어떤 순서·구성으로 쓸 것인가 (1문장)
4. search_queries — 이 절을 자료 풀에서 찾을 **검색 질의 2~3개**. 규칙:
   - 절 제목을 그대로 쓰지 마라. 이미 기본 질의로 던진다 — 여기엔 **절 제목에 없는
     말**(기관명·지표명·제도명·영문 표기)을 넣어 다른 각도를 만든다.
   - 같은 틀이 여러 장에 반복되는 목차에서는(예: 장마다 "개요"·"시사점") 그 장의
     대상이 무엇인지를 질의에 반드시 넣어라 — 안 넣으면 장 안의 절들이 같은 자료를
     받는다.
   - 짧게. 검색어 나열이지 문장이 아니다(각 30자 안팎).
   - 해외 자료가 필요한 절은 영문 질의를 하나 섞어라.

목차 전체에 대해:
- chapters: 장마다 goal 1문장.
- flows: 앞 절의 산출을 받아 쓰는 관계. from/to는 "장.절" 표기,
  carries에 무엇을 받는지(지표·목록·기준)를 구체적으로.
- orphans: 어느 절과도 연결이 없는 절("장.절" 표기). 판단 없이 나열만 —
  독립적이어도 정상일 수 있다.
- query_splits: 입력 duplicate_queries에 있는 절들에만, 서로 갈라질 검색 질의를 제안.
  중복이 없으면 빈 배열.

규칙:
- 사실·수치·기관 통계를 지어내지 마라. 이것은 계획이지 본문이 아니다.
- source_strategy에는 **외부에서 찾을 자료만** 적는다. 앞 절의 산출을 받아 쓰는 관계는
  flows에만 표현하고, source_strategy에 "N.M절의 결과를 내부 자료로 활용" 같은 지시를
  넣지 마라 — 작성 단계는 절 간 산출을 전달받지 못하므로 그 지시는 작성기가 없는 것을
  참조하게 만든다(없는 근거의 창작 유도).
- 간결한 한국어. 절당 3문장 이내.
- 마지막에 아래 형태의 JSON만 출력한다(설명 문장 없이):
{"chapters":[{"chapter":1,"goal":"..."}],
 "sections":[{"chapter":1,"section":1,"goal":"...","source_strategy":"...","writing_plan":"...",
   "search_queries":["...","..."]}],
 "flows":[{"from":"1.2","to":"1.3","carries":"..."}],
 "orphans":["4.1"],
 "query_splits":[{"section":"1.3","query":"..."}]}"""


# 절당 질의 상한. 검색 왕복이 그만큼 늘고, 절 질의 집합 전체 상한
# (retrieval.section.MAX_SECTION_QUERIES)에서 기본·핵심 포인트 몫을 남겨야 한다.
MAX_BRIEF_QUERIES = 3
MAX_BRIEF_QUERY_CHARS = 80


def _clean_queries(raw: Any) -> list[str]:
    """LLM이 낸 검색 질의 목록을 다듬는다 — 문장·중복·과장 길이를 걷어낸다.

    계획 산출을 그대로 검색에 던지므로 여기서 안 다듬으면 잡음이 그대로 질의가 된다.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())[:MAX_BRIEF_QUERY_CHARS].strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= MAX_BRIEF_QUERIES:
            break
    return out


def _compact_input(brief: dict[str, Any]) -> str:
    """브리프 → LLM 입력. 화면과 같은 재료만 싣는다(계획 근거가 화면에서 검증 가능하게)."""
    return json.dumps(
        {
            "topic": brief.get("topic", ""),
            "sections": [
                {
                    "label": f"{s['chapter_number']}.{s['section_number']}",
                    "chapter": s.get("chapter_title", ""),
                    "title": s.get("title", ""),
                    "direction": s.get("direction", ""),
                    "key_points": s.get("key_points", []),
                    "search_query": s.get("search_query", ""),
                }
                for s in brief.get("sections", [])
            ],
            "duplicate_queries": [
                {
                    "query": g.get("query", ""),
                    "sections": [x["label"] for x in g.get("sections", [])],
                }
                for g in brief.get("duplicate_queries", [])
            ],
        },
        ensure_ascii=False,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """응답에서 JSON 오브젝트 추출 — ```json``` 블록 우선, 없으면 마지막 {...}."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced[-1] if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate(raw: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any] | None:
    """LLM 산출을 실제 목차에 대조해 아는 절만 남긴다.

    없는 절 번호를 지어내면(환각) 그 항목만 버린다 — 화면에 유령 절이 뜨는 것보다
    항목 하나 빠지는 쪽이 낫다. 전부 버려지면 None(생성 실패와 동일 취급).
    """
    known = {f"{s['chapter_number']}.{s['section_number']}" for s in brief.get("sections", [])}
    known_chapters = {s["chapter_number"] for s in brief.get("sections", [])}
    dup_labels = {
        x["label"] for g in brief.get("duplicate_queries", []) for x in g.get("sections", [])
    }

    sections = []
    for s in raw.get("sections") or []:
        if not isinstance(s, dict):
            continue
        try:
            label = f"{int(s['chapter'])}.{int(s['section'])}"
        except (KeyError, TypeError, ValueError):
            continue
        if label not in known:
            continue
        sections.append(
            {
                "chapter": int(s["chapter"]),
                "section": int(s["section"]),
                "goal": str(s.get("goal") or ""),
                "source_strategy": str(s.get("source_strategy") or ""),
                "writing_plan": str(s.get("writing_plan") or ""),
                "search_queries": _clean_queries(s.get("search_queries")),
            }
        )
    if not sections:
        return None

    chapters = [
        {"chapter": int(c["chapter"]), "goal": str(c.get("goal") or "")}
        for c in (raw.get("chapters") or [])
        if isinstance(c, dict)
        and isinstance(c.get("chapter"), int)
        and c["chapter"] in known_chapters
    ]
    flows = [
        {"from": str(f["from"]), "to": str(f["to"]), "carries": str(f.get("carries") or "")}
        for f in (raw.get("flows") or [])
        if isinstance(f, dict) and str(f.get("from")) in known and str(f.get("to")) in known
    ]
    orphans = [str(o) for o in (raw.get("orphans") or []) if str(o) in known]
    query_splits = [
        {"section": str(q["section"]), "query": str(q["query"]).strip()}
        for q in (raw.get("query_splits") or [])
        if isinstance(q, dict)
        and str(q.get("section")) in dup_labels
        and str(q.get("query") or "").strip()
    ]
    return {
        "chapters": chapters,
        "sections": sections,
        "flows": flows,
        "orphans": orphans,
        "query_splits": query_splits,
    }


async def generate_ai_plan(
    brief: dict[str, Any],
    *,
    model: str,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
    client: LLMClient | None = None,
) -> dict[str, Any] | None:
    """브리프에 얹을 AI 실행 계획 1콜. 어떤 실패든 None(게이트는 결정적 내용으로 뜬다)."""
    try:
        llm = client or create_llm_client()
        n_sections = len(brief.get("sections", []))
        request = CompletionRequest(
            model=model,
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=_compact_input(brief))],
            max_tokens=_max_tokens(n_sections),
            temperature=0.2,
        )
        with token_context(user_id=user_id, project_id=project_id, operation="design_brief"):
            response = await llm.complete(request)
        raw = _extract_json(response.content)
        if raw is None:
            # stop_reason이 length류면 상한 절단 — _TOKENS_PER_SECTION을 올려야 한다는 신호.
            logger.warning(
                "brief_ai.parse_failed",
                project_id=str(project_id),
                stop_reason=response.stop_reason,
                n_sections=n_sections,
                content_tail=response.content[-160:],
            )
            return None
        plan = _validate(raw, brief)
        if plan is None:
            logger.warning("brief_ai.no_valid_sections", project_id=str(project_id))
        return plan
    except Exception:
        logger.warning("brief_ai.generate_failed", project_id=str(project_id), exc_info=True)
        return None
