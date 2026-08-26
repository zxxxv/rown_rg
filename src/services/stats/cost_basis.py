"""절 하나를 다시 쓰면 얼마인가 — **그 보고서 자기 실측**으로 답한다.

**왜 일반 단가가 아닌가**: 실측(2026-08-26, 운영 DB)에서 절당 비용이 프로젝트마다
$0.40~$1.34로 3.4배 벌어졌다. 모델 등급·절 분량·재료 양이 다 다르기 때문이다. 하나의
평균값을 화면에 박으면 어떤 보고서에서는 3배 과소, 어떤 보고서에서는 3배 과대가 된다.
"예상 $2.1"이 실제 $7이 되는 화면은 없느니만 못하다.

**행 하나가 절 하나가 아니다**: token_usage의 section_write:2.3 행은 **LLM 콜 단위**다
(20절짜리 보고서에 236행). 절당 비용은 distinct operation으로 접어야 나온다 — 이걸
빠뜨리면 절당 $0.11 같은 6배 낮은 숫자가 나온다.

**모를 때는 모른다고 한다**: 아직 한 번도 안 쓴 보고서는 실측이 없다. 같은 모델 등급의
다른 보고서 평균으로 대신하되 그 사실을 라벨로 밝히고, 그것도 없으면 None을 준다.
화면은 "예상 비용을 아직 알 수 없습니다"라고 말하면 된다 — 지어낸 숫자보다 낫다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project import Project
from src.db.models.token_usage import TokenUsage

logger = structlog.get_logger(__name__)

# 절 작성 1건이 남기는 operation 접두 — candidates.py가 "section_write:2.3"으로 찍는다.
_SECTION_WRITE_PREFIX = "section_write:"

CostBasisKind = Literal["project", "model", "none"]


@dataclass(frozen=True)
class CostBasis:
    """절당 비용의 근거. per_section_usd가 None이면 "아직 모른다"는 뜻이다."""

    per_section_usd: float | None
    n_sections_measured: int
    basis: CostBasisKind
    # 이 보고서가 지금까지 쓴 총액 — 예상치를 읽을 때 견줄 기준이 된다.
    spent_usd: float


async def _per_section(session: AsyncSession, *, project_id: UUID | None, model_mode: str | None):
    """(절당 비용, 잰 절 수) — 표본이 없으면 (None, 0).

    project_id를 주면 그 보고서만, 안 주면 같은 모델 등급의 모든 보고서를 잰다.
    """
    stmt = select(
        func.count(distinct(TokenUsage.operation)),
        func.sum(TokenUsage.cost_usd),
    ).where(TokenUsage.operation.like(f"{_SECTION_WRITE_PREFIX}%"))
    if project_id is not None:
        stmt = stmt.where(TokenUsage.project_id == project_id)
    else:
        if not model_mode:
            return None, 0
        # 같은 등급으로 돌린 보고서들 — 등급이 단가와 분량을 함께 좌우한다.
        # model_mode는 컬럼이 아니라 config 안에 산다(생성 폼이 거기에 쓴다).
        stmt = stmt.join(Project, Project.id == TokenUsage.project_id).where(
            Project.config["model_mode"].astext == model_mode
        )
    n_sections, total = (await session.execute(stmt)).one()
    n_sections = int(n_sections or 0)
    if n_sections == 0:
        return None, 0
    return float(Decimal(total or 0)) / n_sections, n_sections


async def measure_cost_basis(session: AsyncSession, project: Project) -> CostBasis:
    """이 보고서의 절당 비용 실측 — 없으면 같은 모델 등급 평균, 그것도 없으면 None."""
    spent = float(
        Decimal(
            (
                await session.execute(
                    select(func.sum(TokenUsage.cost_usd)).where(TokenUsage.project_id == project.id)
                )
            ).scalar_one_or_none()
            or 0
        )
    )

    per, n = await _per_section(session, project_id=project.id, model_mode=None)
    if per is not None:
        return CostBasis(
            per_section_usd=per, n_sections_measured=n, basis="project", spent_usd=spent
        )

    model_mode = (project.config or {}).get("model_mode")
    per, n = await _per_section(session, project_id=None, model_mode=model_mode)
    if per is not None:
        return CostBasis(per_section_usd=per, n_sections_measured=n, basis="model", spent_usd=spent)

    return CostBasis(per_section_usd=None, n_sections_measured=0, basis="none", spent_usd=spent)
