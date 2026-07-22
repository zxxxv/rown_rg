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

from dataclasses import dataclass
from functools import lru_cache

import structlog

from src.core.config import settings
from src.core.types import SectionPlan
from src.prompts import AnalystSpec, load_analyst, load_component

logger = structlog.get_logger(__name__)

# 기본 작성 규칙 — 인용 계약([번호])과 근거 제한.
BASE_SYSTEM = (
    "너는 정부·공공 보고서의 한 섹션을 작성하는 전문 작성자다. "
    "반드시 제공된 근거 자료만 사용하고, 각 주장 끝에 근거를 [번호]로 인용하라. "
    "근거에 없는 수치·고유명사·주장은 절대 쓰지 마라."
)

DEFAULT_MAX_TOKENS = 2048


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
def _style_rules() -> str:
    return load_component("agent_writing_style")


@lru_cache(maxsize=64)
def _analyst_or_none(name: str) -> AnalystSpec | None:
    """이름으로 에이전트 스펙 조회 — 카탈로그에 없으면 경고 후 건너뛴다(파이프라인 불중단)."""
    try:
        return load_analyst(name)
    except KeyError:
        logger.warning("writer_context.unknown_analyst", analyst=name)
        return None


def build_writer_context(section: SectionPlan) -> WriterContext:
    """SectionPlan → 작성 컨텍스트. 페르소나 → 기본 규칙 → 문체 순으로 시스템 조립."""
    spec = next((s for s in map(_analyst_or_none, section.analysts) if s is not None), None)

    parts = [BASE_SYSTEM]
    if spec is not None:
        parts.append(spec.prompt)
    parts.append(_style_rules())
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
    volume = spec.volume_target if spec is not None else None
    if volume is not None:
        min_chars, max_chars = volume.min_chars, volume.max_chars
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
