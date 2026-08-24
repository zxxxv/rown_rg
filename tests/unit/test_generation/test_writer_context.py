"""WriterContext 검증 — 페르소나·방향·분량 목표가 작성 프롬프트에 주입되는지.

실제 프롬프트 카탈로그(src/prompts)를 그대로 읽는다 — 카탈로그가 곧 계약이므로
스텁하지 않는다. LLM 호출은 stub client로 대체한다.
"""

from __future__ import annotations

from uuid import uuid4

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.core.config import settings
from src.core.types import RetrievedChunk, SectionPlan
from src.prompts import list_analysts, load_analyst, load_component
from src.services.generation.candidates import generate_section_candidates
from src.services.generation.writer_context import (
    BASE_SYSTEM,
    CHARS_PER_EVIDENCE,
    DEFAULT_MAX_TOKENS,
    MIN_SCALED_CHARS,
    build_writer_context,
    scale_for_evidence,
)
from src.workflows.write_loop import plan_from_payload, section_plan_payload

# 실카탈로그 앵커 — a17 정책분석 (volume_target: 15000~22500자)
_ANALYST = "정책분석"
# 두 번째 앵커 — 다관점 절(에이전트 2개 이상) 검증용
_ANALYST2 = "시장분석"
# guidance 블록 구분자(빈 줄 하나)
_SEP = chr(10) * 2
_STYLE = load_component("agent_writing_style")


def _section(**kwargs) -> SectionPlan:
    return SectionPlan(chapter_number=1, section_number=2, title="분석", **kwargs)


class TestBuildWriterContext:
    def test_free_topic_defaults(self):
        """자유 주제(배정 없음): 기본 규칙 + 문체만, 게이트 경계는 기본값 위임."""
        ctx = build_writer_context(_section())
        assert ctx.system.startswith(BASE_SYSTEM)
        assert _STYLE in ctx.system
        # 회사 표준 규칙(출처·시각자료)도 writer 시스템에 결합된다(2026-08-05 배선).
        assert load_component("agent_source_rules") in ctx.system
        assert load_component("agent_visual_rules") in ctx.system
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

    def test_unknown_names_dropped_valid_kept(self):
        spec = load_analyst(_ANALYST)
        assert spec.volume_target is not None
        ctx = build_writer_context(_section(analysts=["존재하지않는에이전트", _ANALYST]))
        assert spec.prompt in ctx.system
        assert ctx.min_chars == spec.volume_target.min_chars

    def test_multiple_analysts_all_injected(self):
        """배정이 여럿이면 모두 반영 — 첫 개만 쓰면 나머지는 거짓 스위치가 된다."""
        first, second = load_analyst(_ANALYST), load_analyst(_ANALYST2)
        assert first.volume_target is not None and second.volume_target is not None
        ctx = build_writer_context(_section(analysts=[_ANALYST, _ANALYST2]))
        assert first.prompt in ctx.system
        assert second.prompt in ctx.system
        assert _ANALYST in ctx.system and _ANALYST2 in ctx.system
        # 분량은 합산이 아니라 최댓값 — 합치면 절 하나가 감당 못 할 목표가 된다.
        assert ctx.min_chars == max(first.volume_target.min_chars, second.volume_target.min_chars)

    def test_injected_catalog_wins_over_file(self):
        """개인 에이전트(DB) 주입분이 파일 카탈로그보다 우선."""
        mine = load_analyst(_ANALYST).model_copy(update={"prompt": "내가 만든 페르소나 지침"})
        ctx = build_writer_context(_section(analysts=[_ANALYST]), {_ANALYST: mine})
        assert "내가 만든 페르소나 지침" in ctx.system
        assert load_analyst(_ANALYST).prompt not in ctx.system


class TestVolumeInstruction:
    """분량 지시는 volume_target에서 생성한다 — 에이전트 본문에는 더 이상 없다."""

    def test_volume_line_present_for_assigned_agent(self):
        spec = load_analyst(_ANALYST)
        assert spec.volume_target is not None
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        line = next(x for x in ctx.guidance.split(_SEP) if x.startswith("목표 분량:"))
        assert f"{spec.volume_target.min_chars:,}" in line

    def test_no_volume_line_without_target(self):
        ctx = build_writer_context(_section())
        assert "목표 분량:" not in ctx.guidance

    def test_catalog_prompts_no_longer_carry_volume_text(self):
        # 본문과 필드가 21종 전부 어긋나 있었다(2026-08-10) — 단일 진실은 필드다.
        assert all("분량 가이드" not in a.prompt for a in list_analysts())

    def test_section_volume_overrides_agent(self):
        """절 단위 지정이 정본 — 에이전트는 여러 절이 공유하므로 그쪽을 고치면 남의
        절까지 짧아진다(2026-08-24 지시: 시사점은 3~5쪽으로 눌러야 한다)."""
        spec = load_analyst(_ANALYST)
        assert spec.volume_target is not None
        ctx = build_writer_context(_section(analysts=[_ANALYST], min_chars=4500, max_chars=7500))
        assert (ctx.min_chars, ctx.max_chars) == (4500, 7500)
        line = next(x for x in ctx.guidance.split(_SEP) if x.startswith("목표 분량:"))
        assert "4,500~7,500자" in line and "A4 3~5페이지" in line
        # 눌러 놓은 절에는 무엇을 덜어낼지 알려 준다 — 본론을 되풀이하지 않게.
        assert "판단·함의로 좁혀라" in line

    def test_no_condense_hint_without_section_cap(self):
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert "판단·함의로 좁혀라" not in ctx.guidance

    def test_scaling_rewrites_the_volume_line(self):
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        scaled = scale_for_evidence(ctx, 4)
        lines = [x for x in scaled.guidance.split(_SEP) if x.startswith("목표 분량:")]
        assert len(lines) == 1  # 옛 줄이 남아 두 개가 되면 모델이 큰 숫자를 따라간다
        assert f"{scaled.min_chars:,}" in lines[0]


class TestRuleInjection:
    def test_default_rules_when_not_injected(self):
        ctx = build_writer_context(_section())
        assert _STYLE in ctx.system

    def test_injected_rules_replace_defaults(self):
        """프로젝트에서 고른 개인 규칙이 회사 표준 자리를 대체한다."""
        ctx = build_writer_context(_section(), None, ["내 문체 규칙: 무조건 존댓말"])
        assert "내 문체 규칙: 무조건 존댓말" in ctx.system
        assert _STYLE not in ctx.system


class TestScaleForEvidence:
    def test_scarce_evidence_lowers_target_and_adds_guard(self):
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert ctx.min_chars is not None
        scaled = scale_for_evidence(ctx, 2)
        assert scaled.min_chars == MIN_SCALED_CHARS
        assert "자료 한계" in scaled.guidance
        # 상한은 그대로 — 깎는 건 '최소 이만큼 쓰라'는 강제뿐이다.
        assert scaled.max_chars == ctx.max_chars

    def test_scales_proportionally_to_evidence(self):
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        scaled = scale_for_evidence(ctx, 8)
        assert scaled.min_chars == 8 * CHARS_PER_EVIDENCE

    def test_plentiful_evidence_unchanged(self):
        ctx = build_writer_context(_section(analysts=[_ANALYST]))
        assert ctx.min_chars is not None
        scaled = scale_for_evidence(ctx, ctx.min_chars // CHARS_PER_EVIDENCE + 1)
        assert scaled == ctx

    def test_no_volume_target_untouched(self):
        ctx = build_writer_context(_section())
        assert scale_for_evidence(ctx, 0) == ctx

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
