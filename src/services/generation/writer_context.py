"""섹션 작성 컨텍스트 — 플래너 산출물과 회사 표준 문체를 작성 프롬프트로 결합.

프리셋 경로에서 플래너가 SectionPlan에 채운 direction·key_points·analysts를
후보 생성에 주입한다:
- 배정된 분석 에이전트의 페르소나 프롬프트(전문성)와 분량 목표(volume_target)는
  첫 유효 에이전트를 따른다(섹션당 대표 1명 — 프리셋 관례).
- 회사 표준 개조식 문체(components/agent_writing_style)는 모든 섹션에 공통 적용.
  이 규칙이 [번호] 인용을 요구하므로 기존 인용 계약과 정합하고, 개조식 마커
  (□ ㅇ - *)는 services/export/report.py의 렌더러가 그대로 소화한다.
- 자유 주제(배정 없음)는 기본 시스템 + 문체 규칙만으로 동작한다.

volume_target은 정적 게이트의 길이 경계와 생성 max_tokens에 반영하되,
비용 폭주 방지를 위해 settings.write_max_tokens로 캡한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache

import structlog

from src.core.config import settings
from src.core.types import SectionPlan
from src.prompts import AnalystSpec, load_analyst, load_component

logger = structlog.get_logger(__name__)

# 기본 작성 규칙 — 정체성 + 무관용 사실 원칙(agent_global_system의 핵심을 흡수).
# ※ Tier 1/2/3 멀티에이전트 프레이밍은 현재 아키텍처(config-driven)와 안 맞아 배선하지 않고,
#    출처 강제·환각 금지 원칙만 여기 인라인으로 반영한다.
BASE_SYSTEM = (
    "너는 (주)로운인사이트의 정부·공공·R&D 기획 보고서의 한 섹션을 작성하는 전문 작성자다. "
    "무관용 사실 원칙을 지켜라: 반드시 제공된 근거 자료만 사용하고, 모든 데이터·수치·고유명사·"
    "주장의 끝에 근거를 [번호]로 인용하라. 근거에 없는 내용은 절대 지어내지 마라(환각 금지). "
    "신뢰할 근거를 찾을 수 없으면 그럴듯하게 채우지 말고, 해당 항목을 생략하거나 한계를 명시하라."
)

# writer 시스템에 결합할 회사 표준 규칙 조각(순서 고정): 출처 사용 → 시각자료 → 개조식 문체.
_RULE_COMPONENTS = ("agent_source_rules", "agent_visual_rules", "agent_writing_style")

DEFAULT_MAX_TOKENS = 2048

# 근거 청크 1개가 감당할 수 있는 본문 분량(문자). 검색된 인용 가능 청크 수 ×
# 이 값이 분량 목표의 상한이 된다.
CHARS_PER_EVIDENCE = 750
# 재료가 아무리 적어도 이 아래로는 안 깎는다(절 하나의 최소 골격).
MIN_SCALED_CHARS = 1500

# 본문에 '자료가 부족하다'는 말을 쓰게 하지 않는다 — 납품 보고서 본문에 들어갈
# 문장이 아니다. 부족 사실은 sections.meta 플래그로 빼내 화면에서만 알린다.
_SCARCITY_GUARD = (
    "이 절은 확보된 근거 자료가 적다. 분량을 맞추려고 근거에 없는 수치·연도·기관명·"
    "사업명을 만들어 넣지 마라. 확보된 사실만으로 쓰고 그만큼에서 끝내라 — 짧은 절은 "
    "결함이 아니지만 지어낸 수치는 치명적 결함이다. "
    "단, 자료가 부족하다는 사실 자체는 본문에 쓰지 마라('자료 한계', '확보되지 않아' 같은 "
    "메타 서술 금지) — 보고서 본문은 결과만 담고, 부족 여부는 시스템이 따로 표시한다."
)


@dataclass(frozen=True)
class WriterContext:
    """한 섹션의 후보 생성에 쓰이는 프롬프트·분량 컨텍스트."""

    system: str
    guidance: str  # 유저 프롬프트에 붙일 방향·핵심포인트 블록 ("" 가능)
    max_tokens: int
    # 정적 게이트 길이 경계 — None이면 게이트 기본값 사용
    min_chars: int | None
    max_chars: int | None


@lru_cache(maxsize=1)
def _rule_block() -> str:
    """회사 표준 규칙 조각을 순서대로 결합 — 출처·시각자료·개조식 문체."""
    return "\n\n".join(load_component(name) for name in _RULE_COMPONENTS)


@lru_cache(maxsize=64)
def _analyst_or_none(name: str) -> AnalystSpec | None:
    """이름으로 파일 카탈로그 조회 — 없으면 경고 후 건너뛴다(파이프라인 불중단)."""
    try:
        return load_analyst(name)
    except KeyError:
        logger.warning("writer_context.unknown_analyst", analyst=name)
        return None


def _resolve(name: str, catalog: dict[str, AnalystSpec] | None) -> AnalystSpec | None:
    """주입 카탈로그(개인 포함) 우선, 없으면 파일 카탈로그."""
    if catalog is not None and name in catalog:
        return catalog[name]
    return _analyst_or_none(name)


def scale_for_evidence(ctx: WriterContext, n_evidence: int) -> WriterContext:
    """검색된 근거 수에 맞춰 분량 목표를 깎는다(재료 없는 절의 수치 창작 방지).

    자료가 0~2건인 절에 12,000자를 요구하면 모델은 빈칸을 지어낸 수치로 메우고,
    옆 청크의 [번호]를 붙여 인용처럼 보이게 만든다 — 실측에서 자료 0건 절의 수치
    33%가 근거에 없었다(2026-08-09 예타 런). 목표를 재료에 비례시키고 프롬프트에
    '짧게 끝내라' 가드를 넣어, 부족을 창작이 아니라 짧은 절로 드러나게 한다.

    n_evidence는 인용 가능한 leaf 청크 수다(RAPTOR 요약은 인용 불가라 제외).
    """
    if ctx.min_chars is None:
        return ctx
    cap = max(MIN_SCALED_CHARS, n_evidence * CHARS_PER_EVIDENCE)
    if cap >= ctx.min_chars:
        return ctx
    logger.info(
        "writer_context.scaled_for_evidence",
        n_evidence=n_evidence,
        min_chars=ctx.min_chars,
        scaled=cap,
    )
    guidance = "\n\n".join(x for x in (ctx.guidance, _SCARCITY_GUARD) if x)
    # max_chars는 그대로 둔다 — 상한이라 재료가 많은 절과 계약이 같아야 하고,
    # 깎아야 하는 건 '최소 이만큼 쓰라'는 강제뿐이다.
    return replace(ctx, guidance=guidance, min_chars=cap)


def build_writer_context(
    section: SectionPlan, catalog: dict[str, AnalystSpec] | None = None
) -> WriterContext:
    """SectionPlan → 작성 컨텍스트. 페르소나 → 기본 규칙 → 문체 순으로 시스템 조립.

    catalog: id·name → 스펙 매핑(개인 에이전트 포함). 없으면 파일 카탈로그만 본다.
    개인 에이전트는 DB에 있어 이 모듈이 직접 못 읽으므로 호출부(stages.write)가
    resolve_analysts로 만들어 주입한다 — 그전엔 개인 에이전트를 배정해도 '알 수 없는
    에이전트'로 무시돼 기본 프롬프트로 쓰였다(2026-08-09 실측 거짓 스위치).

    배정이 여러 개면 모두 반영한다: 페르소나를 순서대로 잇고, 분량 목표는 각 목표의
    최댓값을 쓴다(합산하면 절 하나가 감당 못 할 분량이 된다).
    """
    specs = [x for x in (_resolve(n, catalog) for n in section.analysts) if x is not None]

    parts = [BASE_SYSTEM]
    if len(specs) == 1:
        parts.append(specs[0].prompt)
    elif specs:
        # 다관점 절 — 어떤 관점을 함께 써야 하는지 못 박고 페르소나를 잇는다.
        names = " · ".join(x.name for x in specs)
        parts.append(
            f"이 절은 다음 {len(specs)}개 관점을 모두 반영해 작성한다: {names}.\n"
            "각 관점을 별도 대주제로 나눠 다루되, 같은 사실을 관점만 바꿔 반복하지 마라."
        )
        parts.extend(x.prompt for x in specs)
    parts.append(_rule_block())
    system = "\n\n".join(parts)

    guidance_lines: list[str] = []
    if section.direction:
        guidance_lines.append(f"작성 방향: {section.direction}")
    if section.key_points:
        points = "\n".join(f"- {k}" for k in section.key_points)
        guidance_lines.append(f"반드시 다룰 핵심 포인트:\n{points}")

    max_tokens = DEFAULT_MAX_TOKENS
    min_chars: int | None = None
    max_chars: int | None = None
    volumes = [s.volume_target for s in specs if s.volume_target is not None]
    if volumes:
        min_chars = max(v.min_chars for v in volumes)
        max_chars = max(v.max_chars for v in volumes)
        volume = max(volumes, key=lambda v: v.max_chars)
        # 한국어 1문자≈1토큰을 상한 근사로 잡고 설정 캡 적용 — 운영 품질 모드에서만
        # WRITE_MAX_TOKENS를 올려 전체 분량 목표를 실현한다.
        max_tokens = max(DEFAULT_MAX_TOKENS, min(volume.max_chars, settings.write_max_tokens))

    return WriterContext(
        system=system,
        guidance="\n\n".join(guidance_lines),
        max_tokens=max_tokens,
        min_chars=min_chars,
        max_chars=max_chars,
    )
