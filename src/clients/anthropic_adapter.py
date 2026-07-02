from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from src.clients.base import (
    CompletionRequest,
    CompletionResponse,
    WebSearchConfig,
    WebSource,
)
from src.clients.base_adapter import BaseLLMAdapter, RetryKind

# 서버 도구 루프가 pause_turn으로 끊길 때 재호출하는 최대 횟수(무한루프 가드)
_MAX_TOOL_TURNS = 8
# provider-중립 web_search → Anthropic 서버 도구 버전(설치 SDK가 지원하는 최신 변형)
_WEB_SEARCH_TYPE = "web_search_20260209"
_WEB_FETCH_TYPE = "web_fetch_20260209"


class AnthropicAdapter(BaseLLMAdapter):
    provider = "anthropic"

    def _create_client(self, api_key: str) -> Any:
        return AsyncAnthropic(api_key=api_key)

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
        tools = self._build_web_tools(cfg)

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
                # 서버 도구 루프 한도 → assistant content blocks를 그대로 돌려보내 이어간다.
                anth_messages.append({"role": "assistant", "content": result.content})
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
    def _build_web_tools(cfg: WebSearchConfig) -> list[dict[str, Any]]:
        search: dict[str, Any] = {
            "type": _WEB_SEARCH_TYPE,
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
            tools.append({"type": _WEB_FETCH_TYPE, "name": "web_fetch", "max_uses": cfg.max_uses})
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
                        if getattr(doc, "title", None) and not s.get("title"):
                            s["title"] = doc.title

    @staticmethod
    def _extract_doc_text(doc: Any) -> str | None:
        # DocumentBlock.source = PlainTextSource(type="text", data=...) | Base64PDFSource
        source = getattr(doc, "source", None)
        if source is None:
            return None
        if getattr(source, "type", "") == "text":
            return getattr(source, "data", None)
        return None  # PDF/base64 등은 PoC 범위 밖
