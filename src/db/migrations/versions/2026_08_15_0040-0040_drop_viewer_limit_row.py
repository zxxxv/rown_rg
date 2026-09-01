"""0040 viewer 기본 한도 행 삭제 — 뷰어 한도는 설정 대상이 아니다(코드 상수 $0 고정).

뷰어는 읽기 전용 역할(생성·실행 전부 require_writer 차단)이라 LLM 비용 경로가 없다.
$0은 "이 역할 이름으로 LLM 호출이 나가면 그 자체가 이상 신호"라는 선언이다
(2026-08-15 사용자 결정). 설정 화면 란과 화이트리스트에서 함께 제거됐으므로,
시딩돼 있던 행(50)을 지워 폴백(core.limit.DEFAULT_ROLE_LIMIT_USD.viewer = 0)이
단일 진실이 되게 한다. 감사 이력(quota_settings_history)은 지우지 않는다.

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY = "DEFAULT_LIMIT_VIEWER_USD"


def upgrade() -> None:
    op.execute(f"DELETE FROM quota_settings WHERE key = '{_KEY}'")


def downgrade() -> None:
    # 옛 시딩값(50) 복원 — 사용자 수정값은 알 수 없으므로 시딩값으로 되돌린다.
    op.execute(
        "INSERT INTO quota_settings (key, value) "
        f"VALUES ('{_KEY}', '50') ON CONFLICT (key) DO NOTHING"
    )
