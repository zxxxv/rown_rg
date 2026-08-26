"""0049 단계 값 되짚어 채우기 — 컬럼이 하던 거짓말을 한 번에 지운다.

projects.status 하나가 "어디까지 왔나"(진척)와 "지금 뭐가 도나"(실행)를 겸했다. 러너가
멈춘 뒤에도 마지막 실행 단계가 남아, 본문이 다 쓰인 보고서가 목록에서 "자료 검색 중"으로
보였다(2026-08-26 운영 DB 실측: 8건 중 3건이 산출물과 어긋남).

앞으로는 전이마다 산출물에서 되짚어 새긴다(services/projects/derive). 이 마이그레이션은
**이미 어긋나 있는 과거 행**을 같은 규칙으로 한 번 맞춘다 — 안 하면 옛 프로젝트는
누군가 열어 볼 때까지 거짓말을 계속한다.

되짚는 규칙(derive_stage와 같은 순서):
  보관·취소는 사람이 만든 사실이라 건드리지 않는다.
  확정(completed_at) → completed
  본문 있음 → reviewing   (사람이 볼 차례. 다시 열기가 오는 자리)
  청크 있음 → writing     (색인은 됐고 작성이 남았다)
  자료 있음 → researching
  목차 있음 → planning
  그 밖 → created

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKFILL = """
UPDATE projects p SET status = CASE
    WHEN p.completed_at IS NOT NULL THEN 'completed'
    WHEN EXISTS (
        SELECT 1 FROM sections s
        WHERE s.project_id = p.id AND length(btrim(s.content)) > 0
    ) THEN 'reviewing'
    WHEN EXISTS (SELECT 1 FROM chunks c WHERE c.project_id = p.id) THEN 'writing'
    WHEN EXISTS (SELECT 1 FROM project_sources ps WHERE ps.project_id = p.id) THEN 'researching'
    WHEN p.config ? 'outline' OR p.config ? 'design_brief' THEN 'planning'
    ELSE 'created'
END
WHERE p.status NOT IN ('archived', 'cancelled')
"""


def upgrade() -> None:
    op.execute(_BACKFILL)


def downgrade() -> None:
    # 되돌릴 옛 값이 없다 — 그 값이 애초에 거짓말이었다. 파생 규칙은 그대로 남으므로
    # 다운그레이드해도 다음 전이에서 같은 값이 다시 새겨진다.
    pass
