"""WriterContext 검증 — 페르소나·방향·분량 목표가 작성 프롬프트에 주입되는지.

실제 프롬프트 카탈로그(src/prompts)를 그대로 읽는다 — 카탈로그가 곧 계약이므로
스텁하지 않는다. LLM 호출은 stub client로 대체한다.
"""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.config import settings
from src.core.types import RetrievedChunk, SectionPlan
from src.prompts import load_analyst, load_component
from src.services.generation.candidates import generate_section_candidates
from src.services.generation.writer_context import (
    BASE_SYSTEM,
    DEFAULT_MAX_TOKENS,
    build_writer_context,
)
from src.workflows.write_loop import plan_from_payload, section_plan_payload

# 실카탈로그 앵커 — a17 정책분석 (volume_target: 15000~22500자)
_ANALYST = "정책분석"
_STYLE = load_component("agent_writing_style")


def _section(**kwargs) -> SectionPlan:
    return SectionPlan(chapter_number=1, section_number=2, title="분석", **kwargs)


class TestBuildWriterContext:
    def test_free_topic_defaults(self):
        """자유 주제(배정 없음): 기본 규칙 + 문체만, 게이트 경계는 기본값 위임."""
        ctx = build_writer_context(_section())
        assert ctx.system.startswith(BASE_SYSTEM)
        assert _STYLE in ctx.system
        assert ctx.guidance == ""
        assert ctx.max_tokens == DEFAULT_MAX_TOKENS
        assert ctx.min_chars is None
        assert ctx.max_chars is None

    def test_analyst_persona_and_volume_target(self):
        spec = load_analyst(_ANALYST)
        assert spec.volume_target is not None
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert spec.prompt in ctx.system
        # 조립 순서: 기본 규칙 → 페르소나 → 문체
        assert ctx.system.index(BASE_SYSTEM) < ctx.system.index(spec.prompt)
        assert ctx.system.index(spec.prompt) < ctx.system.index(_STYLE)
        assert ctx.min_chars == spec.volume_target.min_chars
        assert ctx.max_chars == spec.volume_target.max_chars

    def test_unknown_analyst_skipped(self):
        """카탈로그에 없는 이름은 경고 후 무시 — 자유 주제와 동일하게 동작."""
        ctx = build_writer_context(_section(analysts=["존재하지않는에이전트"]))
        assert ctx.system.startswith(BASE_SYSTEM)
        assert ctx.min_chars is None

    def test_first_valid_analyst_wins(self):
        spec = load_analyst(_ANALYST)
        assert spec.volume_target is not None
        ctx = build_writer_context(_section(analysts=["존재하지않는에이전트", _ANALYST]))
        assert spec.prompt in ctx.system
        assert ctx.min_chars == spec.volume_target.min_chars

    def test_guidance_from_direction_and_key_points(self):
        ctx = build_writer_context(
            _section(direction="규제 현황 중심으로", key_points=["법령 계층", "샌드박스"])
        )
        assert "작성 방향: 규제 현황 중심으로" in ctx.guidance
        assert "- 법령 계층" in ctx.guidance
        assert "- 샌드박스" in ctx.guidance

    def test_guidance_direction_only(self):
        ctx = build_writer_context(_section(direction="개요 수준"))
        assert ctx.guidance == "작성 방향: 개요 수준"


class TestMaxTokensResolution:
    def test_capped_by_write_max_tokens(self, monkeypatch):
        """volume_target이 커도 settings.write_max_tokens가 상한(비용 폭주 방지)."""
        monkeypatch.setattr(settings, "write_max_tokens", 4096)
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert ctx.max_tokens == 4096

    def test_quality_mode_follows_volume_target(self, monkeypatch):
        monkeypatch.setattr(settings, "write_max_tokens", 30000)
        spec = load_analyst(_ANALYST)
        assert spec.volume_target is not None
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert ctx.max_tokens == spec.volume_target.max_chars

    def test_floor_is_default(self, monkeypatch):
        """캡을 기본치보다 낮게 잡아도 최소 DEFAULT_MAX_TOKENS는 보장."""
        monkeypatch.setattr(settings, "write_max_tokens", 1000)
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert ctx.max_tokens == DEFAULT_MAX_TOKENS


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        return CompletionResponse(
            content="본문 [1]",
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            stop_reason="stop",
        )


def _chunks() -> list[RetrievedChunk]:
    return [RetrievedChunk(chunk_id=uuid4(), source_id=uuid4(), content="근거", score=0.9)]


class TestCandidatesWiring:
    """generate_section_candidates가 WriterContext를 실제 요청에 반영하는지."""

    async def test_context_flows_into_request(self):
        stub = _StubClient()
        section = _section(analysts=[_ANALYST], direction="규제 중심")
        await generate_section_candidates(section, _chunks(), n=1, client=stub)
        req = stub.calls[0]
        assert req.system is not None
        assert load_analyst(_ANALYST).prompt in req.system
        assert "작성 방향: 규제 중심" in req.messages[0].content
        assert req.max_tokens == build_writer_context(section).max_tokens

    async def test_explicit_max_tokens_overrides_context(self):
        stub = _StubClient()
        await generate_section_candidates(
            _section(analysts=[_ANALYST]), _chunks(), n=1, client=stub, max_tokens=512
        )
        assert stub.calls[0].max_tokens == 512


class TestPayloadRoundTrip:
    """게이트 payload 직렬화가 플래너 산출물을 보존하는지 (resume 경로 생존)."""

    def test_round_trip_preserves_planner_outputs(self):
        plan = [
            _section(direction="방향", key_points=["p1", "p2"], analysts=[_ANALYST]),
        ]
        restored = plan_from_payload({"section_plan": section_plan_payload(plan)})
        assert restored[0].section_id == plan[0].section_id
        assert restored[0].direction == "방향"
        assert restored[0].key_points == ["p1", "p2"]
        assert restored[0].analysts == [_ANALYST]

    def test_legacy_payload_without_new_fields(self):
        """구버전 payload(필드 없음)도 기본값으로 복원 — 진행 중 프로젝트 호환."""
        legacy = {
            "section_plan": [
                {
                    "section_id": str(uuid4()),
                    "chapter_number": 1,
                    "section_number": 1,
                    "title": "개요",
                }
            ]
        }
        restored = plan_from_payload(legacy)
        assert restored[0].direction == ""
        assert restored[0].key_points == []
        assert restored[0].analysts == []
