"""AI 실행 계획(brief_ai) — 파싱·검증·실패 격리.

원칙 검증: LLM 산출은 실제 목차에 대조해 아는 절만 남기고(유령 절 차단),
어떤 실패든 None으로 끝난다(게이트를 막지 않는다).
"""

from __future__ import annotations

import pytest

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.services.generation.brief_ai import _validate, generate_ai_plan


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


class _BrokenClient:
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise RuntimeError("api down (모의)")


_BRIEF = {
    "topic": "글로벌 탄소규제 동향",
    "sections": [
        {
            "chapter_number": 1,
            "section_number": 1,
            "chapter_title": "글로벌 RE100",
            "title": "개요",
            "direction": "",
            "key_points": [],
            "search_query": "글로벌 RE100 개요",
        },
        {
            "chapter_number": 2,
            "section_number": 1,
            "chapter_title": "EU CBAM",
            "title": "개요",
            "direction": "",
            "key_points": [],
            "search_query": "EU CBAM 개요",
        },
    ],
    "duplicate_queries": [{"query": "개요", "sections": [{"label": "1.1"}, {"label": "2.1"}]}],
}

_GOOD_JSON = (
    '{"chapters":[{"chapter":1,"goal":"RE100 현황"}],'
    '"sections":[{"chapter":1,"section":1,"goal":"참여 현황 제시",'
    '"source_strategy":"The Climate Group 연차보고서","writing_plan":"현황→시사점"},'
    '{"chapter":9,"section":9,"goal":"유령 절"}],'
    '"flows":[{"from":"1.1","to":"2.1","carries":"기준 정의"},'
    '{"from":"1.1","to":"9.9","carries":"없는 절로"}],'
    '"orphans":["2.1","9.9"],'
    '"query_splits":[{"section":"1.1","query":"글로벌 RE100 참여 현황"},'
    '{"section":"3.3","query":"중복에 없는 절"}]}'
)


class TestValidate:
    def test_아는_절만_남긴다(self) -> None:
        import json

        plan = _validate(json.loads(_GOOD_JSON), _BRIEF)
        assert plan is not None
        assert [s["goal"] for s in plan["sections"]] == ["참여 현황 제시"]  # 9.9 유령 제거
        assert plan["flows"] == [{"from": "1.1", "to": "2.1", "carries": "기준 정의"}]
        assert plan["orphans"] == ["2.1"]

    def test_갈래_제안은_중복_절에만(self) -> None:
        import json

        plan = _validate(json.loads(_GOOD_JSON), _BRIEF)
        assert plan is not None
        assert plan["query_splits"] == [{"section": "1.1", "query": "글로벌 RE100 참여 현황"}]

    def test_검색_질의를_다듬어_싣는다(self) -> None:
        """계획 산출이 그대로 검색 질의가 되므로 문장·중복·과장 길이를 걷어낸다."""
        out = _validate(
            {
                "sections": [
                    {
                        "chapter": 1,
                        "section": 1,
                        "goal": "g",
                        "search_queries": [
                            "  CBAM 전환기간 신고 의무  ",
                            "cbam 전환기간 신고 의무",  # 대소문자만 다른 중복
                            "가" * 200,  # 과장 길이
                            "CBAM reporting obligation",
                            "네 번째는 상한에 걸려 잘린다",
                        ],
                    }
                ]
            },
            _BRIEF,
        )
        assert out is not None
        qs = out["sections"][0]["search_queries"]
        assert qs[0] == "CBAM 전환기간 신고 의무"
        assert len(qs) <= 3
        assert all(len(q) <= 80 for q in qs)

    def test_검색_질의가_없어도_계획은_유효하다(self) -> None:
        """계획이 질의를 안 내도 실행은 절 제목·핵심 포인트로 그대로 돈다."""
        out = _validate({"sections": [{"chapter": 1, "section": 1, "goal": "g"}]}, _BRIEF)
        assert out is not None
        assert out["sections"][0]["search_queries"] == []

    def test_유효_절이_하나도_없으면_None(self) -> None:
        assert _validate({"sections": [{"chapter": 9, "section": 9}]}, _BRIEF) is None


class TestGenerate:
    @pytest.mark.asyncio
    async def test_정상_응답이_계획으로_변환된다(self) -> None:
        plan = await generate_ai_plan(
            _BRIEF, model="test-model", client=_StubClient(f"```json\n{_GOOD_JSON}\n```")
        )
        assert plan is not None
        assert plan["chapters"] == [{"chapter": 1, "goal": "RE100 현황"}]

    @pytest.mark.asyncio
    async def test_JSON_아닌_응답은_None(self) -> None:
        assert (
            await generate_ai_plan(_BRIEF, model="m", client=_StubClient("계획은 다음과 같다…"))
            is None
        )

    @pytest.mark.asyncio
    async def test_API_예외도_None(self) -> None:
        """게이트를 막지 않는다 — 어떤 실패든 결정적 브리프만으로 뜬다."""
        assert await generate_ai_plan(_BRIEF, model="m", client=_BrokenClient()) is None


class TestOwnership:
    def test_소유권은_아는_절에만_중복_토픽은_처음_것만(self) -> None:
        out = _validate(
            {
                "sections": [{"chapter": 1, "section": 1, "goal": "g"}],
                "topic_ownership": [
                    {"topic": "RE100 실태조사", "owner": "1.1"},
                    {"topic": "유령 절 소유", "owner": "9.9"},
                    {"topic": "re100 실태조사", "owner": "2.1"},
                    {"topic": "", "owner": "1.1"},
                ],
            },
            _BRIEF,
        )
        assert out is not None
        assert out["topic_ownership"] == [{"topic": "RE100 실태조사", "owner": "1.1"}]

    def test_소유권_상한(self) -> None:
        out = _validate(
            {
                "sections": [{"chapter": 1, "section": 1, "goal": "g"}],
                "topic_ownership": [{"topic": f"토픽{i}", "owner": "1.1"} for i in range(20)],
            },
            _BRIEF,
        )
        assert out is not None
        assert len(out["topic_ownership"]) == 10


class TestSalvage:
    """상한 절단 구제 - 마지막 항목이 잘려도 앞의 완성 항목은 살린다."""

    def test_잘린_JSON에서_완성_절을_건진다(self) -> None:
        from src.services.generation.brief_ai import _extract_json

        truncated = (
            '{"chapters":[{"chapter":1,"goal":"g"}],'
            '"sections":[{"chapter":1,"section":1,"goal":"참여 현황"},'
            '{"chapter":2,"section":1,"goal":"CBAM 개'
        )
        raw = _extract_json(truncated)
        assert raw is not None
        assert raw["sections"][0]["goal"] == "참여 현황"

    def test_sections_없는_조각은_구제하지_않는다(self) -> None:
        from src.services.generation.brief_ai import _extract_json

        assert _extract_json('{"chapters":[{"chapter":1,"goal":"g"}],"flo') is None

    def test_문자열_안_괄호는_구제를_흔들지_않는다(self) -> None:
        from src.services.generation.brief_ai import _extract_json

        truncated = (
            '{"sections":[{"chapter":1,"section":1,"goal":"괄호 {중첩} 포함"},'
            '{"chapter":2,"section":1,"goal":"잘'
        )
        raw = _extract_json(truncated)
        assert raw is not None
        assert raw["sections"][0]["goal"] == "괄호 {중첩} 포함"
