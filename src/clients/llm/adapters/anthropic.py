from typing import Any

import structlog
from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from src.clients.llm.adapters.base import BaseLLMAdapter, RetryKind
from src.clients.llm.base import (
    CompletionRequest,
    CompletionResponse,
    WebSearchConfig,
    WebSource,
)
from src.core.config import settings

logger = structlog.get_logger(__name__)

# 서버 도구 루프가 pause_turn으로 끊길 때 재호출하는 최대 횟수(무한루프 가드)
_MAX_TOOL_TURNS = 8
# 회수 본문 총 예산(토큰) — 200k 컨텍스트에서 시스템·검색결과·생성분을 뺀 안전선.
# 페이지당 상한 = min(cfg.max_content_tokens, 예산 // max_uses)로 곱이 항상 예산 이하.
_FETCH_BUDGET_TOKENS = 100_000
# provider-중립 web_search → Anthropic 서버 도구 버전.
# _20260209(동적 필터링)는 Opus 4.6+/Sonnet 4.6+ 전용 — Haiku 등 구형 모델은
# basic 변형을 써야 한다(아니면 400). 모델별 선택은 _web_tool_types.
_WEB_SEARCH_TYPE = "web_search_20260209"
_WEB_FETCH_TYPE = "web_fetch_20260209"
_BASIC_WEB_SEARCH_TYPE = "web_search_20250305"
_BASIC_WEB_FETCH_TYPE = "web_fetch_20250910"


def _web_tool_types(model: str) -> tuple[str, str]:
    """모델이 지원하는 웹도구 변형 선택 — Haiku는 basic, 그 외는 동적 필터링 변형."""
    if "haiku" in model:
        return _BASIC_WEB_SEARCH_TYPE, _BASIC_WEB_FETCH_TYPE
    return _WEB_SEARCH_TYPE, _WEB_FETCH_TYPE


class AnthropicAdapter(BaseLLMAdapter):
    provider = "anthropic"

    def _create_client(self, api_key: str) -> Any:
        # max_retries=0: 재시도는 BaseLLMAdapter가 소유한다. SDK가 타임아웃된
        # 서버도구 턴을 내부에서 재실행하면 응답 없이 서버측 과금만 반복된다.
        return AsyncAnthropic(
            api_key=api_key,
            timeout=settings.llm_client_timeout_s,
            max_retries=0,
        )

    def _classify_error(self, exc: Exception) -> RetryKind | None:
        if isinstance(exc, RateLimitError):
            return "rate_limit"
        if isinstance(exc, APIConnectionError):
            return "retryable"
        if isinstance(exc, APIStatusError):
            return "fatal" if exc.status_code < 500 else "retryable"
        if isinstance(exc, APIError):
            return "retryable"
        return None

    async def _call_provider(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None
        if request.web_search is not None:
            return await self._call_with_web_search(request)

        # Anthropic Messages API: system은 messages가 아니라 별도 파라미터
        anth_messages = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": anth_messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.system:
            kwargs["system"] = self._cacheable_system(request.system)

        result = await self._client.messages.create(**kwargs)
        text_blocks = [getattr(b, "text", "") for b in result.content]
        return CompletionResponse(
            content="".join(text_blocks),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_input_tokens=getattr(result.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_input_tokens=getattr(result.usage, "cache_creation_input_tokens", 0) or 0,
            model=result.model,
            stop_reason=result.stop_reason or "end_turn",
        )

    @staticmethod
    def _cacheable_system(system: str) -> list[dict[str, Any]]:
        """system을 블록 리스트로 감싸 끝에 캐시 브레이크포인트를 찍는다.

        문자열 system엔 브레이크포인트를 둘 수 없어 같은 페르소나를 쓰는 후속 호출
        (동일 절 후보 2개, 재작성, pm_verify 반복)이 매번 전액을 낸다. 모델별 최소
        캐시 길이(Sonnet 1,024·Haiku 4,096토큰) 미만이면 API가 조용히 무시하므로
        짧은 system에 붙여도 손해는 없다.
        """
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    # web_search(server tool) 경로
    async def _call_with_web_search(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None
        cfg = request.web_search
        assert cfg is not None
        tools = self._build_web_tools(cfg, request.model)

        anth_messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        # 프롬프트 캐싱 브레이크포인트 2단: ① 마지막 요청 메시지 끝(고정 프리픽스),
        # ② top-level 자동 배치(매 턴 마지막 블록). pause_turn 재전송은 이전 턴까지의
        # 대화(회수 본문 포함)를 통째로 다시 보내는데, 캐싱이 없으면 그 전부를 매 턴
        # 전액 재과금당한다 — 수집 비용 지배 구간(2026-08-06 실측 런당 ~$2.4-2.6).
        # ①은 한 턴의 블록 수가 캐시 조회 한도(20블록)를 넘어 ②의 체인이 끊겨도
        # 시스템+도구+요청 프리픽스만은 항상 재사용되게 하는 안전망이다.
        if anth_messages and isinstance(anth_messages[-1]["content"], str):
            anth_messages[-1]["content"] = [
                {
                    "type": "text",
                    "text": anth_messages[-1]["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        sources: dict[str, dict[str, Any]] = {}  # url -> WebSource 필드(부분 누적)
        text_parts: list[str] = []
        in_tok = out_tok = cached_tok = cache_write_tok = 0
        stop_reason = "end_turn"
        model = request.model

        for _ in range(_MAX_TOOL_TURNS):
            kwargs: dict[str, Any] = {
                "model": request.model,
                "messages": anth_messages,
                "max_tokens": request.max_tokens,
                "tools": tools,
                "cache_control": {"type": "ephemeral"},
            }
            if request.system:
                kwargs["system"] = self._cacheable_system(request.system)
            # temperature는 의도적으로 생략: Opus 4.8/4.7 등은 temperature를 400으로 거부한다.

            result = await self._client.messages.create(**kwargs)
            in_tok += result.usage.input_tokens
            out_tok += result.usage.output_tokens
            cached_tok += getattr(result.usage, "cache_read_input_tokens", 0) or 0
            cache_write_tok += getattr(result.usage, "cache_creation_input_tokens", 0) or 0
            self._collect_sources(result.content, sources)
            text_parts += [
                getattr(b, "text", "") for b in result.content if getattr(b, "type", "") == "text"
            ]
            stop_reason = result.stop_reason or "end_turn"
            model = result.model

            if stop_reason == "pause_turn":
                # 서버 도구 루프 한도 → assistant content blocks를 돌려보내 이어간다.
                # 단, 대형 PDF 회수 결과는 생략(아래 _sanitize_for_resend 참고).
                anth_messages.append(
                    {"role": "assistant", "content": self._sanitize_for_resend(result.content)}
                )
                continue
            break

        if stop_reason == "max_tokens":
            # 최종 텍스트(매니페스트 JSON)가 잘리면 신뢰도·매칭섹션이 조용히
            # 소실된다(회수 본문은 web_sources로 살아남음) — 침묵하지 않는다.
            logger.warning(
                "anthropic.web_search.truncated",
                model=model,
                max_tokens=request.max_tokens,
                n_sources=len(sources),
            )
        web_sources = [WebSource(**fields) for fields in sources.values()]
        return CompletionResponse(
            content="".join(text_parts),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached_tok,
            cache_write_input_tokens=cache_write_tok,
            model=model,
            stop_reason=stop_reason,
            web_sources=web_sources,
        )

    @staticmethod
    def _build_web_tools(cfg: WebSearchConfig, model: str) -> list[dict[str, Any]]:
        search_type, fetch_type = _web_tool_types(model)
        search: dict[str, Any] = {
            "type": search_type,
            "name": "web_search",
            "max_uses": cfg.max_uses,
        }
        if cfg.user_country:
            search["user_location"] = {"type": "approximate", "country": cfg.user_country}
        if cfg.allowed_domains:
            search["allowed_domains"] = cfg.allowed_domains
        if cfg.blocked_domains:
            search["blocked_domains"] = cfg.blocked_domains
        tools: list[dict[str, Any]] = [search]
        if cfg.fetch_pages:
            # 불변식: 회수 본문 총량(fetch 횟수 × 페이지당 상한)이 컨텍스트 예산을
            # 넘지 못하게 페이지당 상한을 자동 축소한다. max_uses만 올리고 상한을
            # 잊어도 prompt too long(200k 초과)이 구조적으로 재발하지 않게 하는 장치.
            per_page = min(
                cfg.max_content_tokens,
                max(4_000, _FETCH_BUDGET_TOKENS // max(1, cfg.max_uses)),
            )
            fetch: dict[str, Any] = {
                "type": fetch_type,
                "name": "web_fetch",
                "max_uses": cfg.max_uses,
                "max_content_tokens": per_page,
            }
            # 도메인 필터는 검색·회수 양쪽에 일관 적용 — 검색만 거르면 모델이
            # 검색 밖 URL(본문 내 링크 등)을 회수해 필터를 우회할 수 있다.
            if cfg.allowed_domains:
                fetch["allowed_domains"] = cfg.allowed_domains
            if cfg.blocked_domains:
                fetch["blocked_domains"] = cfg.blocked_domains
            tools.append(fetch)
        return tools

    def _collect_sources(self, blocks: Any, sources: dict[str, dict[str, Any]]) -> None:
        """Anthropic server-tool 결과 블록을 WebSource 필드로 정규화해 url별로 누적."""
        for b in blocks:
            btype = getattr(b, "type", "")
            if btype == "web_search_tool_result":
                content = getattr(b, "content", None)
                if isinstance(content, list):  # 성공 시 list[WebSearchResultBlock] (에러면 객체)
                    for r in content:
                        if getattr(r, "type", "") != "web_search_result":
                            continue
                        url = getattr(r, "url", "")
                        if not url:
                            continue
                        s = sources.setdefault(url, {"url": url})
                        if getattr(r, "title", None) and not s.get("title"):
                            s["title"] = r.title
                        if getattr(r, "page_age", None):
                            s["page_age"] = r.page_age
            elif btype == "web_fetch_tool_result":
                content = getattr(b, "content", None)  # WebFetchBlock | 에러 블록
                if getattr(content, "type", "") == "web_fetch_result":
                    url = getattr(content, "url", "")
                    doc = getattr(content, "content", None)  # DocumentBlock
                    text = self._extract_doc_text(doc)
                    if url and text:
                        s = sources.setdefault(url, {"url": url})
                        s["content_md"] = text
                        doc_title = getattr(doc, "title", None)
                        if doc_title and not s.get("title"):
                            s["title"] = doc_title

    @staticmethod
    def _sanitize_for_resend(blocks: Any) -> list[Any]:
        """pause_turn 재전송에서 PDF 회수 결과를 안내 텍스트로 치환한다.

        web_fetch가 회수한 100페이지 초과 PDF는 재전송 시 API가 400으로 거부해
        챕터 수집 전체를 죽인다. 크기 임계값(500k)으로 거르던 1차 시도는 얇은
        장문 PDF를 놓쳐 재발했다(2026-08-03 두 차례 실측) — 페이지 수를 클라이언트
        에서 알 방법이 없으므로 base64 PDF는 전부 치환한다. 우리 추출 경로는 PDF
        본문을 쓰지 않아(_extract_doc_text가 PDF는 None) 잃는 것이 없다.
        """
        out: list[Any] = []
        n_stripped = 0
        for b in blocks:
            try:
                if getattr(b, "type", "") == "web_fetch_tool_result":
                    content = getattr(b, "content", None)
                    doc = getattr(content, "content", None)
                    source = getattr(doc, "source", None)
                    if getattr(source, "type", "") == "base64":
                        replaced = b.model_dump()
                        replaced["content"]["content"]["source"] = {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": "[PDF 본문 생략 — 재전송 제한 회피. URL로만 참조하라]",
                        }
                        out.append(replaced)
                        n_stripped += 1
                        continue
            except Exception:
                # 치환 실패는 원본 재전송으로 폴백하되 반드시 흔적을 남긴다 —
                # 조용히 삼키면 PDF 400 재발 시 원인 추적이 불가능하다(1차 교훈).
                logger.warning("anthropic.pdf_sanitize_failed", exc_info=True)
            out.append(b)
        if n_stripped:
            logger.info("anthropic.pdf_stripped_on_resend", n=n_stripped)
        return out

    @staticmethod
    def _extract_doc_text(doc: Any) -> str | None:
        # DocumentBlock.source = PlainTextSource(type="text", data=...) | Base64PDFSource
        source = getattr(doc, "source", None)
        if source is None:
            return None
        if getattr(source, "type", "") == "text":
            return getattr(source, "data", None)
        return None  # PDF/base64 등은 PoC 범위 밖
