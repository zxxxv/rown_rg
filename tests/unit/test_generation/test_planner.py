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


# 산업동향보고서 프리셋 골격: ch01=[시장분석, 시장분석], ch02=[STEEP분석], ...
_PRESET_MANIFEST = json.dumps(
    {
        "sections": [
            {
                "chapter": 1,
                "section": 1,
                "title": "수소전기차 산업 정의",
                "direction": "산업 범위와 분류 체계",
                "key_points": ["산업 분류", "범위"],
            },
            {"chapter": 1, "section": 2, "title": "수소전기차 밸류체인"},
            {"chapter": 1, "section": 3, "title": "추가 개요 섹션"},
            {"chapter": 2, "section": 1, "title": "수소경제 거시환경"},
            {"chapter": 9, "section": 1, "title": "프리셋 범위 밖 챕터"},
        ]
    },
    ensure_ascii=False,
)


class TestPlanSectionsWithPreset:
    async def test_uses_toc_system_prompt_and_preset_skeleton(self):
        client = _StubClient(_PRESET_MANIFEST)
        await plan_sections("수소전기차", "산업동향보고서", client=client)
        request = client.requests[0]
        assert "전문 보고서 기획자" in (request.system or "")  # toc_system 역할 프롬프트
        assert "산업동향보고서" in request.messages[0].content  # 프리셋 골격 주입

    async def test_assigns_analysts_by_chapter_position(self):
        plan = await plan_sections(
            "수소전기차", "산업동향보고서", client=_StubClient(_PRESET_MANIFEST)
        )
        assert plan[0].analysts == ["시장분석"]  # ch01 1번째 섹션
        assert plan[1].analysts == ["시장분석"]  # ch01 2번째 섹션
        assert plan[2].analysts == ["시장분석"]  # ch01 초과분 → 마지막 섹션에 클램프
        assert plan[3].analysts == ["STEEP분석"]  # ch02 1번째 섹션
        assert plan[4].analysts == []  # 프리셋 범위 밖 챕터는 배정 없음

    async def test_parses_direction_and_key_points(self):
        plan = await plan_sections(
            "수소전기차", "산업동향보고서", client=_StubClient(_PRESET_MANIFEST)
        )
        assert plan[0].direction == "산업 범위와 분류 체계"
        assert plan[0].key_points == ["산업 분류", "범위"]
        assert plan[1].direction == ""  # 누락 시 기본값

    async def test_free_report_type_keeps_generic_path(self):
        client = _StubClient(_FENCED)
        plan = await plan_sections("인구 고령화 대응", "blank", client=client)
        assert "목차 설계자" in (client.requests[0].system or "")  # 기존 자유 목차 프롬프트
        assert all(p.analysts == [] for p in plan)
