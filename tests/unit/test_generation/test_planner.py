"""섹션 플래너 검증 — JSON 매니페스트 파싱·검증·캡 (실LLM 없음)."""

from __future__ import annotations

import json

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.services.generation.planner import MAX_SECTIONS, plan_sections


class _StubClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(
            content=self._text,
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


_FENCED = (
    "목차를 설계했습니다.\n"
    "```json\n"
    '{"sections": [\n'
    '  {"chapter": 1, "section": 1, "title": "고령화 추이와 전망"},\n'
    '  {"chapter": 1, "section": 2, "title": "정책 현황"},\n'
    '  {"chapter": 2, "section": 1, "title": "비용편익 분석"}\n'
    "]}\n"
    "```"
)


class TestPlanSections:
    async def test_parses_fenced_manifest(self):
        plan = await plan_sections(
            "인구 고령화 대응", "policy_research", client=_StubClient(_FENCED)
        )
        assert [(p.chapter_number, p.section_number) for p in plan] == [(1, 1), (1, 2), (2, 1)]
        assert plan[0].title == "고령화 추이와 전망"

    async def test_parses_bare_json(self):
        text = json.dumps(
            {"sections": [{"chapter": 1, "section": 1, "title": "개요"}]}, ensure_ascii=False
        )
        plan = await plan_sections("주제", "blank", client=_StubClient(text))
        assert len(plan) == 1

    async def test_skips_malformed_items(self):
        text = json.dumps(
            {
                "sections": [
                    {"chapter": 0, "section": 1, "title": "잘못된 장번호"},
                    {"chapter": 1, "section": 1, "title": "  개요  "},
                    {"chapter": 1, "section": 2},  # title 누락
                    "문자열 항목",
                ]
            },
            ensure_ascii=False,
        )
        plan = await plan_sections("주제", "blank", client=_StubClient(text))
        assert len(plan) == 1
        assert plan[0].title == "개요"

    async def test_raises_when_no_valid_sections(self):
        with pytest.raises(ValueError):
            await plan_sections("주제", "blank", client=_StubClient("목차는 다음과 같습니다."))

    async def test_truncates_over_cap(self):
        sections = [
            {"chapter": 1, "section": i, "title": f"섹션 {i}"} for i in range(1, MAX_SECTIONS + 10)
        ]
        text = json.dumps({"sections": sections}, ensure_ascii=False)
        plan = await plan_sections("주제", "blank", client=_StubClient(text))
        assert len(plan) == MAX_SECTIONS
