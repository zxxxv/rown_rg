from typing import Any

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

# 서버 도구 루프가 pause_turn으로 끊길 때 재호출하는 최대 횟수(무한루프 가드)
_MAX_TOOL_TURNS = 8
# 회수 본문 총 예산(토큰) — 200k 컨텍스트에서 시스템·검색결과·생성분을 뺀 안전선.
# 페이지당 상한 = min(cfg.max_content_tokens, 예산 // max_uses)로 곱이 항상 예산 이하.
_FETCH_BUDGET_TOKENS = 100_000
# pause_turn 재전송에서 이보다 큰 base64 PDF는 본문 생략(100페이지 제한 400 회피)
_PDF_RESEND_MAX_CHARS = 500_000
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
            kwargs["system"] = request.system

        result = await self._client.messages.create(**kwargs)
        text_blocks = [getattr(b, "text", "") for b in result.content]
        return CompletionResponse(
            content="".join(text_blocks),
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_input_tokens=getattr(result.usage, "cache_read_input_tokens", 0) or 0,
            model=result.model,
            stop_reason=result.stop_reason or "end_turn",
        )

    # web_search(server tool) 경로
    async def _call_with_web_search(self, request: CompletionRequest) -> CompletionResponse:
        assert self._client is not None
        cfg = request.web_search
        assert cfg is not None
        tools = self._build_web_tools(cfg, request.model)

        anth_messages: list[dict[str, Any]] = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        sources: dict[str, dict[str, Any]] = {}  # url -> WebSource 필드(부분 누적)
        text_parts: list[str] = []
        in_tok = out_tok = cached_tok = 0
        stop_reason = "end_turn"
        model = request.model

        for _ in range(_MAX_TOOL_TURNS):
            kwargs: dict[str, Any] = {
                "model": request.model,
                "messages": anth_messages,
                "max_tokens": request.max_tokens,
                "tools": tools,
            }
            if request.system:
                kwargs["system"] = request.system
            # temperature는 의도적으로 생략: Opus 4.8/4.7 등은 temperature를 400으로 거부한다.

            result = await self._client.messages.create(**kwargs)
            in_tok += result.usage.input_tokens
            out_tok += result.usage.output_tokens
            cached_tok += getattr(result.usage, "cache_read_input_tokens", 0) or 0
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

        web_sources = [WebSource(**fields) for fields in sources.values()]
        return CompletionResponse(
            content="".join(text_parts),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached_tok,
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
            tools.append(
                {
                    "type": fetch_type,
                    "name": "web_fetch",
                    "max_uses": cfg.max_uses,
                    "max_content_tokens": per_page,
                }
            )
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
        """pause_turn 재전송에서 대형 PDF 회수 결과를 안내 텍스트로 치환한다.

        web_fetch가 회수한 100페이지 초과 PDF는 재전송 시 API가 400으로 거부해
        챕터 수집 전체를 죽인다(2026-08-03 실측: 6챕터 중 3챕터 실패). 우리 추출
        경로는 PDF 본문을 쓰지 않으므로(_extract_doc_text가 PDF는 None) 큰 PDF를
        생략해도 잃는 것이 거의 없다. 페이지 수는 클라이언트에서 알 수 없어
        base64 길이를 근사 기준으로 쓴다.
        """
        out: list[Any] = []
        for b in blocks:
            try:
                if getattr(b, "type", "") == "web_fetch_tool_result":
                    content = getattr(b, "content", None)
                    doc = getattr(content, "content", None)
                    source = getattr(doc, "source", None)
                    data = getattr(source, "data", None)
                    if (
                        getattr(source, "type", "") == "base64"
                        and isinstance(data, str)
                        and len(data) > _PDF_RESEND_MAX_CHARS
                    ):
                        replaced = b.model_dump()
                        replaced["content"]["content"]["source"] = {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": "[대형 PDF 본문 생략 — 재전송 제한 회피. URL로만 참조하라]",
                        }
                        out.append(replaced)
                        continue
            except Exception:  # noqa: BLE001 — 어떤 블록이든 원본 그대로 재전송이 기본
                pass
            out.append(b)
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
