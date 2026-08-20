"""서사 사슬(실험 C) - 요약 파싱·주입 포맷. 수치 금지·근거 아님 프레이밍이 계약이다."""

from __future__ import annotations

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.services.generation.narrative_chain import (
    format_chain_injection,
    summarize_section,
)


class _StubClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


class TestSummarize:
    async def test_json_response_becomes_entry(self) -> None:
        out = await summarize_section(
            label="1.2",
            title="과제",
            content="본문",
            client=_StubClient(
                '{"summary":"제도 대응 과제를 다룸","topics":["대응 과제","인지도"]}'
            ),
        )
        assert out == {
            "section": "1.2",
            "title": "과제",
            "summary": "제도 대응 과제를 다룸",
            "topics": ["대응 과제", "인지도"],
        }

    async def test_broken_json_returns_none(self) -> None:
        assert (
            await summarize_section(
                label="1.2", title="t", content="c", client=_StubClient("요약: 어쩌고")
            )
            is None
        )

    async def test_client_error_returns_none(self) -> None:
        class _Broken:
            async def complete(self, request):
                raise RuntimeError("down")

        assert (
            await summarize_section(label="1.2", title="t", content="c", client=_Broken()) is None
        )


class TestInjection:
    def test_empty_prior_is_empty(self) -> None:
        assert format_chain_injection([]) == ""

    def test_contains_json_and_guardrail_framing(self) -> None:
        note = format_chain_injection(
            [{"section": "1.1", "title": "현황", "summary": "현황을 다룸", "topics": ["인지도"]}]
        )
        assert '"절": "1.1"' in note
        assert "근거 자료가 아니다" in note
        assert "다시 서술하지" in note
        assert "(출처 n)" in note
