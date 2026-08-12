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
from src.core.config import settings
from src.services.research.pdf_fetch import PdfSourceFetcher, looks_like_pdf

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"  # 가성비 기본(동적필터 지원). Opus 4.8로 승격 가능.

SYSTEM_PROMPT = """너는 보고서 작성을 위한 웹 리서처다.
입력으로 보고서의 주제·종류·세부목차(JSON)가 주어진다.

목표: 각 목차 섹션의 내용을 구성할 수 있는 **신뢰할 만한 웹 출처**를 수집한다.
- web_search로 각 목차 섹션에 관련된 페이지를 찾는다.
- `section_briefs`가 있으면 그 절이 **무엇을 논증해야 하는지·어떤 분석 관점인지**가
  적혀 있다. 절 제목만으로 검색하지 말고 그 방향·관점의 어휘(예: 예산 산출이면 단가·
  적산 기준·유사사업 사업비, 경제성이면 편익 항목·할인율·B/C)를 질의에 넣어라.
- 유용한 페이지는 web_fetch로 **본문 전체를 회수**한다(요약·추측 금지, 실제 내용 근거).
- **단 PDF는 web_fetch 하지 마라**. URL이 `.pdf`로 끝나거나 검색 결과가 PDF 문서임을
  나타내면 회수를 건너뛰고 매니페스트에 URL만 실어라 — 시스템이 따로 내려받아 처리한다.
  (PDF를 회수하면 요청당 100페이지 상한에 걸려 이 챕터 수집이 통째로 실패한다)
- **회수가 최우선이다**: 본문 없이 URL만 나열한 출처는 보고서 작성에 쓰이지 못한다.
  검색 횟수를 아껴서라도 찾은 페이지 중 가장 유용한 것들은 반드시 web_fetch로 회수하라.
  (매니페스트의 출처 수보다 본문 회수 성공 수가 훨씬 중요하다)
- 각 출처에 대해 어느 목차 섹션에 해당하는지, 신뢰도(high/medium/low), 최신성을 판단한다.
- 신뢰도 판정 기준(반드시 이 루브릭을 따른다):
  * high — 정부·공공기관(통계청·부처·지자체 등), 학술지·대학·국책/공인 연구기관, 국제기구
  * medium — 주요 언론사 보도, 산업 협회·시장조사기관 리포트, 기업 공식 발표(IR·백서)
  * low — 개인 블로그·커뮤니티·위키, 출처 불명 집계 사이트, 광고성 콘텐츠
- {scope_rule}
- 글로벌 비교·기술 동향·주요국 정책이 주제에 있으면 최소 3개국 이상의 1차 자료를 모은다.
- 가능하면 최근 3년 이내 자료를 우선한다(최신성).

작업을 마치면 **마지막 메시지에 아래 형식의 JSON만** 출력한다(설명 문장 없이):
```json
{"sources": [
  {"url": "https://...", "title": "...", "reliability": "high|medium|low",
   "sections": ["<입력 목차 항목과 정확히 같은 문자열>", "..."]}
]}
```
- **매니페스트에는 web_fetch로 본문을 회수한 출처 + PDF URL만 싣는다** — 그 외에
  검색 결과로 보기만 한 URL은 넣지 마라(본문 없는 출처는 시스템이 사용하지 못한다).
  PDF는 회수하지 않아도 싣는다(시스템이 내려받는다). 최대 10개.
- sections 값은 반드시 입력 목차의 항목 문자열과 정확히 일치시켜라."""


# 검색 범위 — 프로젝트가 고른다(국내 제도·통계가 본질인 보고서와 해외 기술 동향이
# 본질인 보고서는 정답이 다르다). (지역 힌트, 프롬프트 지시문).
# 화면은 국내/해외 체크박스(다중 선택)다: 국내만=domestic, 해외만=global, 둘 다=all.
SEARCH_SCOPES: dict[str, tuple[str | None, str]] = {
    "domestic": (
        "KR",
        "국내 자료를 우선한다. 정부·공공기관·국책연구기관·주요 언론을 중심으로 모으고, "
        "해외 자료는 비교가 꼭 필요할 때만 보조로 쓴다.",
    ),
    "all": (
        None,
        "국내와 해외 자료를 모두 폭넓게 모은다. **영어 질의를 절반가량 섞어라** — "
        "주제가 한국어라고 한국어로만 검색하면 해외 1차 자료가 통째로 빠진다"
        "(실측: 지역 설정을 풀어도 한국어 질의만 하면 10건 중 8건이 국내였다). "
        "국내는 정부·공공기관·국책연구기관·주요 언론, 해외는 주요국 정부기관·"
        "국제기구(OECD·IEA·EU·IEEE 등)·해외 학술지를 본다.",
    ),
    "global": (
        None,
        "해외 자료를 우선한다. **검색의 2/3 이상을 영어 질의로 하고** 주요국 정부기관·"
        "국제기구·해외 학술지/시장조사기관의 1차 자료를 먼저 모은다. "
        "국내 자료는 대조·적용 맥락으로 보조한다.",
    ),
}
# '반반'(balanced)은 체크박스 개편(2026-08-13)으로 폐지 — 비율 지시가 아니라 '둘 다
# 본다'가 실사용 의도였다. 개편 전에 저장된 프로젝트를 위해 별칭으로만 남긴다.
SEARCH_SCOPES["balanced"] = SEARCH_SCOPES["all"]
# 값이 없는 옛 프로젝트의 폴백 — 개편 전 기본(반반)과 같은 행동을 유지한다.
# 신규 프로젝트는 폼이 항상 값을 쓰므로(기본 국내) 여기 오지 않는다.
DEFAULT_SEARCH_SCOPE = "all"


class ResearchSpec(BaseModel):
    topic: str
    report_type: str
    # 절 제목 목록 — 매니페스트의 matched_sections가 이 문자열과 정확히 일치해야
    # 커버리지 계산이 성립한다(절대 가공하지 말 것).
    outline: list[str]
    # 절별 검색 힌트: "제목 — 방향 (관점: 에이전트)". 질의 품질을 올리려고 프롬프트에만
    # 싣고 매칭에는 쓰지 않는다 — 목차에 있는 방향·핵심포인트·분석 관점이 수집 단계로
    # 전달되지 않아 '예산 산출' 같은 절이 빈손으로 남던 문제(2026-08-09 실측).
    briefs: list[str] = []
    # 검색 범위(domestic|balanced|global) — 프로젝트 설정에서 온다.
    scope: str = DEFAULT_SEARCH_SCOPE


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
    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        pdf_fetcher: PdfSourceFetcher | None = None,
    ):
        # 웹검색=외부 실시간 데이터 → 캐셋 replay 부적합. 단일 관문은 유지하고 mode만 live.
        self.llm = llm or create_llm_client(mode="live")
        # PDF만 우리가 회수한다(HTML은 서버 web_fetch 유지) — 새 의존성 없이
        # 100페이지 상한을 피하는 최소 범위.
        self._pdf_fetcher = pdf_fetcher or PdfSourceFetcher()

    async def collect(
        self,
        spec: ResearchSpec,
        *,
        model: str = DEFAULT_MODEL,
        allowed_domains: list[str] | None = None,
        max_uses: int = 5,
        max_fetch_uses: int | None = None,
        max_tokens: int = 8000,
    ) -> ResearchResult:
        user = json.dumps(
            {
                "topic": spec.topic,
                "report_type": spec.report_type,
                "outline": spec.outline,
                **({"section_briefs": spec.briefs} if spec.briefs else {}),
            },
            ensure_ascii=False,
        )
        country, scope_rule = SEARCH_SCOPES.get(spec.scope, SEARCH_SCOPES[DEFAULT_SEARCH_SCOPE])
        request = CompletionRequest(
            model=model,
            system=SYSTEM_PROMPT.replace("{scope_rule}", scope_rule),
            messages=[Message(role="user", content=user)],
            max_tokens=max_tokens,
            web_search=WebSearchConfig(
                max_uses=max_uses,
                max_fetch_uses=max_fetch_uses,
                fetch_pages=True,
                user_country=settings.research_user_country or country,
                allowed_domains=allowed_domains,
            ),
        )
        response = await self.llm.complete(request)

        manifest = _parse_manifest(response.content)
        sources = _merge_sources(response.web_sources, manifest, spec.outline)
        await self._fill_pdf_bodies(sources)
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

    async def _fill_pdf_bodies(self, sources: list[CollectedSource]) -> None:
        """본문 없는 PDF 출처를 우리가 직접 내려받아 채운다(서버 100페이지 상한 우회).

        모델은 PDF를 web_fetch하지 않도록 지시받으므로 URL만 매니페스트에 실려 온다.
        회수·파싱 실패는 그 출처만 본문 없이 남고, 상위(stages)의 has_usable_content가
        걸러낸다 — 수집 전체를 죽이지 않는다.
        """
        targets = [s.url for s in sources if not s.content_md and looks_like_pdf(s.url)]
        if not targets:
            return
        bodies = await self._pdf_fetcher.fetch_many(targets)
        for source in sources:
            body = bodies.get(source.url)
            if body and not source.content_md:
                source.content_md = body
        logger.info("web_research.pdf_filled", requested=len(targets), filled=len(bodies))


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
