"""수집 챕터 재시도 — 회수(web_fetch)가 원인인 400은 회수 1회로 줄여 다시 부른다.

두 오류가 같은 처방을 쓴다:
- PDF 100페이지 상한 400 (2026-08-06: 12콜 중 6콜 전멸 → 자료 6건)
- 입력 상한 초과 (2026-08-19 스모크: 1,136,960 > 200,000 토큰으로 1장 통째 실패,
  자료 2건. pause_turn 재전송에 회수한 HTML 본문이 누적된 결과)

재호출은 요청을 처음부터 새로 만들므로 누적분이 사라진다 — 그래서 회수만 줄이면 된다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.clients.llm.exceptions import LLMClientError
from src.services.research import ResearchResult, ResearchSpec
from src.workflows import stages

pytestmark = pytest.mark.asyncio

_SPEC = ResearchSpec(topic="주제", report_type="조사분석보고서", outline=["1.1 절"])

PDF_LIMIT_MSG = "400 invalid_request_error: requests may contain a maximum of 100 PDF pages"
OVERFLOW_MSG = "prompt is too long: 1136960 tokens > 200000 maximum"


class _Service:
    """첫 콜은 지정한 오류로 죽고, 두 번째 콜부터 성공하는 수집 서비스."""

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.calls: list[int | None] = []

    async def collect(self, spec, **kw):
        self.calls.append(kw.get("max_fetch_uses"))
        if self.error is not None and len(self.calls) == 1:
            raise self.error
        return ResearchResult(spec=spec, sources=[], manifest={}, coverage_gaps=[])


async def _run(monkeypatch, error: Exception | None) -> _Service:
    service = _Service(error)
    monkeypatch.setattr(stages, "_research_service_factory", lambda: service)
    await stages._collect_chapter(_SPEC, model="m", project_id=uuid4(), chapter=1)
    return service


class TestFetchLimitRetry:
    async def test_context_overflow_retries_with_one_fetch(self, monkeypatch):
        """이 재시도가 없어서 스모크에서 1장이 통째로 날아갔다."""
        service = await _run(monkeypatch, LLMClientError(OVERFLOW_MSG))
        assert len(service.calls) == 2
        assert service.calls[1] == 1  # 회수를 1회로 줄여 다시 부른다

    async def test_pdf_page_limit_still_retries(self, monkeypatch):
        service = await _run(monkeypatch, LLMClientError(PDF_LIMIT_MSG))
        assert len(service.calls) == 2
        assert service.calls[1] == 1

    async def test_success_does_not_retry(self, monkeypatch):
        service = await _run(monkeypatch, None)
        assert len(service.calls) == 1

    async def test_unrelated_error_is_not_retried(self, monkeypatch):
        """회수와 무관한 실패까지 재시도하면 비용만 두 배로 쓴다."""
        service = _Service(LLMClientError("529 overloaded"))
        monkeypatch.setattr(stages, "_research_service_factory", lambda: service)
        with pytest.raises(LLMClientError):
            await stages._collect_chapter(_SPEC, model="m", project_id=uuid4(), chapter=1)
        assert len(service.calls) == 1

    async def test_retry_failure_propagates(self, monkeypatch):
        """재시도는 1회로 캡한다 - 두 번째도 죽으면 그 챕터는 포기하고 올린다."""

        class _AlwaysFails(_Service):
            async def collect(self, spec, **kw):
                self.calls.append(kw.get("max_fetch_uses"))
                raise LLMClientError(OVERFLOW_MSG)

        service = _AlwaysFails(None)
        monkeypatch.setattr(stages, "_research_service_factory", lambda: service)
        with pytest.raises(LLMClientError):
            await stages._collect_chapter(_SPEC, model="m", project_id=uuid4(), chapter=1)
        assert len(service.calls) == 2
