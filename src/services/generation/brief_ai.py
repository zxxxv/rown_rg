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
# 기본 몫 3000: 토픽 소유권 블록(전역, 절 수 무관)이 추가되면서 상향(2026-08-20).
_BASE_TOKENS = 3000
_TOKENS_PER_SECTION = 700
_MAX_TOKENS_CAP = 16000


def _max_tokens(n_sections: int) -> int:
    return min(_MAX_TOKENS_CAP, _BASE_TOKENS + _TOKENS_PER_SECTION * max(1, n_sections))


SYSTEM_PROMPT = """너는 정부·공공 보고서의 실행 계획을 세우는 설계 책임자다.
입력으로 보고서 주제와 이미 확정된 목차(장·절·작성 방향·핵심 포인트·검색 질의)가 JSON으로 주어진다.
입력에 domain_context(이 보고서 유형의 틀과 관행)가 있으면 source_strategy와 search_queries가
그 틀에 맞는 기관·자료 유형·용어를 고르는 데 참고하라.
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
- topic_ownership: 여러 절이 저마다 다시 서술할 위험이 큰 공용 토픽을 골라 **가장
  적합한 절 하나**에 소유를 배정한다(3~8건). 위험 토픽의 전형: 조사·통계의 개요와
  주요 결과, 제도·정책의 배경과 연혁, 방법론 설명 — 특히 여러 장이 같은 대상을
  다른 각도로 다루는 목차에서 반드시 지정하라. topic은 자료명·조사명·제도명 수준으로
  구체적으로 쓴다("현황"·"배경" 같은 일반어 금지). 소유 절이 그 토픽을 정본으로
  완결하고, 다른 모든 절은 참조 한 문장으로 대체하게 된다.
  - **같은 장 안의 절 사이에도 배정하라** — 진단 절과 사례 절처럼 한 장에서 두 절이
    같은 소재를 요구하면 수치 제시권을 한 절에 몰아라(실측: 같은 실태조사 블록이
    한 보고서에 네 번 실렸다).
  - 구조 수준(조사명·제도명·소재)까지만 배정하라. 개별 자료 파일 단위 배정은 자료가
    확정된 뒤 별도 단계가 한다 — 이 시점엔 어떤 자료가 채택될지 확정 전이다.
- outline_overlaps: **같은 소재를 요구하는 절 쌍**과 역할 분담 제안(0~5건). 목차
  자체가 겹침을 만들 때만 적는다 — sections는 "장.절" 2개, material은 겹치는 소재
  (자료명·조사명), proposal은 "1.3=수치 정본, 1.4=사례·해석" 형태의 분담 한 줄.
  이 경고는 게이트에서 사람이 보고 목차를 고칠 근거가 된다.

규칙:
- 사실·수치·기관 통계를 지어내지 마라. 이것은 계획이지 본문이 아니다.
- source_strategy에는 **외부에서 찾을 자료만** 적는다. 앞 절의 산출을 받아 쓰는 관계는
  flows에만 표현하고, source_strategy에 "N.M절의 결과를 내부 자료로 활용" 같은 지시를
  넣지 마라 — 절 간 전달은 flows와 값 주입 경로가 처리하며, source_strategy에 넣으면
  작성기가 전달받지 않은 것을 참조하게 만든다(없는 근거의 창작 유도).
- 간결한 한국어. 절당 3문장 이내.
- 마지막에 아래 형태의 JSON만 출력한다(설명 문장 없이):
{"chapters":[{"chapter":1,"goal":"..."}],
 "sections":[{"chapter":1,"section":1,"goal":"...","source_strategy":"...","writing_plan":"...",
   "search_queries":["...","..."]}],
 "flows":[{"from":"1.2","to":"1.3","carries":"..."}],
 "orphans":["4.1"],
 "query_splits":[{"section":"1.3","query":"..."}],
 "topic_ownership":[{"topic":"제조수출기업 RE100 실태조사","owner":"1.3"}],
 "outline_overlaps":[{"sections":["1.3","1.4"],"material":"RE100 실태조사",
   "proposal":"1.3=수치 정본, 1.4=사례·해석(수치는 참조)"}]}"""


# 절당 질의 상한. 검색 왕복이 그만큼 늘고, 절 질의 집합 전체 상한
# (retrieval.section.MAX_SECTION_QUERIES)에서 기본·핵심 포인트 몫을 남겨야 한다.
MAX_BRIEF_QUERIES = 3
MAX_BRIEF_QUERY_CHARS = 80

# 토픽 소유권 상한 — 열 개를 넘으면 소유권이 아니라 목차 재서술이다.
MAX_OWNERSHIP_TOPICS = 10
MAX_OWNERSHIP_TOPIC_CHARS = 60


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
            # 보고서 유형의 도메인 맥락(프리셋) — 절별 질의·자료 전략이 이 틀을 알아야
            # "무슨 종류의 보고서인가"에 맞는 기관·자료 유형을 고른다.
            "domain_context": brief.get("domain_context", ""),
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
            # (uploads는 싣지 않는다 — 2026-08-21 폐지. 자료명 배정은 코퍼스 확정 후
            # source_canon이 한다. 설계가 자료를 미리 알면 유령 배정이 생긴다.)
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


def _salvage_json(candidate: str) -> dict[str, Any] | None:
    """잘린 JSON에서 완성된 앞부분을 건진다.

    상한 절단(stop_reason=length)이면 통파싱이 실패해 계획 전체가 버려졌다 — 20절
    계획에서 마지막 항목 하나가 잘렸다고 앞의 19절 몫까지 버리는 건 손해가 크다.
    문자열 밖 괄호를 스택으로 추적해, 뒤에서부터 요소 경계('}'/']')마다 잘라 닫는
    괄호를 보충해 파싱을 시도한다. _validate가 어차피 항목 단위로 거르므로 부분
    산출로 충분하다.
    """
    stack: list[str] = []
    in_str = esc = False
    cut_points: list[tuple[int, tuple[str, ...]]] = []
    for i, ch in enumerate(candidate):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            cut_points.append((i, tuple(stack)))
    for i, snap in reversed(cut_points[-80:]):  # 시도 횟수 상한
        closers = "".join("}" if c == "{" else "]" for c in reversed(snap))
        try:
            data = json.loads(candidate[: i + 1] + closers)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("sections"):
            return data
    return None


def _extract_json(text: str) -> dict[str, Any] | None:
    """응답에서 JSON 오브젝트 추출 — ```json``` 블록 우선, 없으면 마지막 {...}.

    통파싱이 실패하면(대개 상한 절단) 완성 항목까지만 건지는 구제 경로를 탄다.
    """
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced[-1] if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if candidate is None and "{" in text:
        candidate = text[text.find("{") :]  # 닫는 괄호가 아예 없는 절단 — 구제만 가능
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        salvaged = _salvage_json(candidate)
        if salvaged is not None:
            logger.info("brief_ai.salvaged_truncated_json", n_sections=len(salvaged["sections"]))
        return salvaged
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
    # 토픽 소유권 — 유령 절(owner가 목차에 없음)·빈 토픽·중복 토픽은 버린다.
    topic_ownership: list[dict[str, str]] = []
    seen_topics: set[str] = set()
    for t in raw.get("topic_ownership") or []:
        if not isinstance(t, dict):
            continue
        topic = " ".join(str(t.get("topic") or "").split())[:MAX_OWNERSHIP_TOPIC_CHARS]
        owner = str(t.get("owner") or "").strip()
        if topic and owner in known and topic.lower() not in seen_topics:
            seen_topics.add(topic.lower())
            topic_ownership.append({"topic": topic, "owner": owner})
        if len(topic_ownership) >= MAX_OWNERSHIP_TOPICS:
            break
    # 목차 소재 겹침 — 유령 절·자기 쌍은 버리고 상한 5. 게이트 카드가 사람에게
    # "목차를 고칠 근거"로 보여준다(작성기 주입 아님 — 처방은 소유권이 담당).
    outline_overlaps: list[dict[str, Any]] = []
    for o in raw.get("outline_overlaps") or []:
        if not isinstance(o, dict):
            continue
        pair = [str(x).strip() for x in (o.get("sections") or []) if str(x).strip() in known]
        material = " ".join(str(o.get("material") or "").split())[:60]
        proposal = " ".join(str(o.get("proposal") or "").split())[:120]
        if len(pair) == 2 and pair[0] != pair[1] and material:
            outline_overlaps.append({"sections": pair, "material": material, "proposal": proposal})
        if len(outline_overlaps) >= 5:
            break
    return {
        "chapters": chapters,
        "sections": sections,
        "flows": flows,
        "orphans": orphans,
        "query_splits": query_splits,
        "topic_ownership": topic_ownership,
        "outline_overlaps": outline_overlaps,
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
