"""웹 리서치 출처 수집 서비스.

"주제 + 보고서 종류 + 세부목차"를 받아, Claude 내장 web_search/web_fetch로
각 목차 섹션을 채울 웹 출처(원문)를 수집하고 provider-중립 구조로 반환한다.

LLM 호출은 factory→LLMRouter 단일 관문을 경유한다(메모리 project_llm_provider_router).
웹검색은 외부 실시간 데이터라 캐셋 replay가 부적합하므로 기본 live 모드 클라이언트를 쓴다.
산출물은 구조화 출력까지이며 DB 적재·청킹은 후속 단계다.

LangGraph 노드는 `await WebResearchService().collect(spec)`만 래핑하면 된다.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel

from src.clients.llm.base import CompletionRequest, LLMClient, Message, WebSearchConfig, WebSource
from src.clients.llm.factory import create_llm_client

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"  # 가성비 기본(동적필터 지원). Opus 4.8로 승격 가능.

SYSTEM_PROMPT = """너는 보고서 작성을 위한 웹 리서처다.
입력으로 보고서의 주제·종류·세부목차(JSON)가 주어진다.

목표: 각 목차 섹션의 내용을 구성할 수 있는 **신뢰할 만한 웹 출처**를 수집한다.
- web_search로 각 목차 섹션에 관련된 페이지를 찾는다.
- 유용한 페이지는 web_fetch로 **본문 전체를 회수**한다(요약·추측 금지, 실제 내용 근거).
- **회수가 최우선이다**: 본문 없이 URL만 나열한 출처는 보고서 작성에 쓰이지 못한다.
  검색 횟수를 아껴서라도 찾은 페이지 중 가장 유용한 것들은 반드시 web_fetch로 회수하라.
  (매니페스트의 출처 수보다 본문 회수 성공 수가 훨씬 중요하다)
- 각 출처에 대해 어느 목차 섹션에 해당하는지, 신뢰도(high/medium/low), 최신성을 판단한다.
- 신뢰도 판정 기준(반드시 이 루브릭을 따른다):
  * high — 정부·공공기관(통계청·부처·지자체 등), 학술지·대학·국책/공인 연구기관, 국제기구
  * medium — 주요 언론사 보도, 산업 협회·시장조사기관 리포트, 기업 공식 발표(IR·백서)
  * low — 개인 블로그·커뮤니티·위키, 출처 불명 집계 사이트, 광고성 콘텐츠
- 한국 맥락이면 공식·정부·학술·주요 언론 등 권위 있는 출처를 우선한다.
- 글로벌 비교가 필요하면 최소 3개국 이상의 사례를 수집한다.
- 가능하면 최근 3년 이내 자료를 우선한다(최신성).

작업을 마치면 **마지막 메시지에 아래 형식의 JSON만** 출력한다(설명 문장 없이):
```json
{"sources": [
  {"url": "https://...", "title": "...", "reliability": "high|medium|low",
   "sections": ["<입력 목차 항목과 정확히 같은 문자열>", "..."]}
]}
```
- **매니페스트에는 web_fetch로 본문을 회수한 출처만 싣는다** — 검색 결과로 보기만 한
  URL은 넣지 마라(본문 없는 출처는 시스템이 사용하지 못한다). 최대 10개.
- sections 값은 반드시 입력 목차의 항목 문자열과 정확히 일치시켜라."""


class ResearchSpec(BaseModel):
    topic: str
    report_type: str
    outline: list[str]


class CollectedSource(BaseModel):
    url: str
    title: str | None = None
    content_md: str | None = None  # web_fetch로 회수한 본문(청킹 대상)
    reliability: str | None = None  # high | medium | low
    matched_sections: list[str] = []
    page_age: str | None = None


class ResearchResult(BaseModel):
    spec: ResearchSpec
    sources: list[CollectedSource]
    manifest: dict  # 모델이 낸 raw 매니페스트(디버그·추적용)
    coverage_gaps: list[str]  # 출처가 하나도 안 붙은 목차 섹션


class WebResearchService:
    def __init__(self, llm: LLMClient | None = None):
        # 웹검색=외부 실시간 데이터 → 캐셋 replay 부적합. 단일 관문은 유지하고 mode만 live.
        self.llm = llm or create_llm_client(mode="live")

    async def collect(
        self,
        spec: ResearchSpec,
        *,
        model: str = DEFAULT_MODEL,
        allowed_domains: list[str] | None = None,
        max_uses: int = 5,
        max_tokens: int = 8000,
    ) -> ResearchResult:
        user = json.dumps(
            {"topic": spec.topic, "report_type": spec.report_type, "outline": spec.outline},
            ensure_ascii=False,
        )
        request = CompletionRequest(
            model=model,
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=user)],
            max_tokens=max_tokens,
            web_search=WebSearchConfig(
                max_uses=max_uses,
                fetch_pages=True,
                user_country="KR",
                allowed_domains=allowed_domains,
            ),
        )
        response = await self.llm.complete(request)

        manifest = _parse_manifest(response.content)
        sources = _merge_sources(response.web_sources, manifest, spec.outline)
        covered = {sec for s in sources for sec in s.matched_sections}
        coverage_gaps = [sec for sec in spec.outline if sec not in covered]

        logger.info(
            "web_research.collected",
            topic=spec.topic,
            model=model,
            n_sources=len(sources),
            n_with_body=sum(1 for s in sources if s.content_md),
            coverage_gaps=len(coverage_gaps),
        )
        return ResearchResult(
            spec=spec, sources=sources, manifest=manifest, coverage_gaps=coverage_gaps
        )


def _parse_manifest(text: str) -> dict:
    """모델 최종 텍스트에서 JSON 매니페스트를 추출. ```json``` 블록 우선, 없으면 마지막 {...}."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced[-1] if fenced else None
    if candidate is None:
        start, end = text.rfind("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if not candidate:
        return {"sources": []}
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {"sources": []}
    except json.JSONDecodeError:
        return {"sources": []}


# 도메인 기반 결정적 신뢰도 상향 — LLM 판정과 무관하게 무조건 high.
# 정부(go.kr)·대학(ac.kr)·공인 연구기관(re.kr)·해외 정부/대학(.gov/.edu)만 —
# or.kr은 사설 단체도 쓸 수 있어 제외(LLM 루브릭 판단에 맡김).
_TRUSTED_DOMAIN_SUFFIXES = (".go.kr", ".ac.kr", ".re.kr", ".gov", ".edu")


def _domain_reliability(url: str) -> str | None:
    """신뢰 도메인이면 'high', 아니면 None(판정 유지)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    for suffix in _TRUSTED_DOMAIN_SUFFIXES:
        if host.endswith(suffix) or host == suffix.lstrip("."):
            return "high"
    return None


def _merge_sources(
    web_sources: list[WebSource], manifest: dict, outline: list[str]
) -> list[CollectedSource]:
    """어댑터가 정규화한 web_sources(본문·title·page_age)에 매니페스트의 섹션·신뢰도를 결합.

    신뢰도는 매니페스트(LLM 루브릭 판정)를 기본으로 하되, 신뢰 도메인
    (_TRUSTED_DOMAIN_SUFFIXES)은 결정적으로 high로 오버라이드한다.
    """
    by_url = {ws.url: ws for ws in web_sources}
    outline_set = set(outline)
    out: list[CollectedSource] = []
    seen: set[str] = set()

    for item in manifest.get("sources", []) or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        ws = by_url.get(url)
        out.append(
            CollectedSource(
                url=url,
                title=item.get("title") or (ws.title if ws else None),
                content_md=ws.content_md if ws else None,
                reliability=_domain_reliability(url) or item.get("reliability"),
                matched_sections=[s for s in (item.get("sections") or []) if s in outline_set],
                page_age=ws.page_age if ws else None,
            )
        )
        seen.add(url)

    # 매니페스트엔 없지만 본문을 회수한 출처도 누락 없이 포함.
    for ws in web_sources:
        if ws.url in seen or not ws.content_md:
            continue
        out.append(
            CollectedSource(
                url=ws.url,
                title=ws.title,
                content_md=ws.content_md,
                reliability=_domain_reliability(ws.url),
                page_age=ws.page_age,
            )
        )
    return out
