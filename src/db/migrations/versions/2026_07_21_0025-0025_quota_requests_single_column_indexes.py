"""add single-column indexes on quota_requests(user_id, status, requested_at)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-21
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_quota_requests_user_id", "quota_requests", ["user_id"])
    op.create_index("ix_quota_requests_status", "quota_requests", ["status"])
    op.create_index("ix_quota_requests_requested_at", "quota_requests", ["requested_at"])


def downgrade() -> None:
    op.drop_index("ix_quota_requests_requested_at", table_name="quota_requests")
    op.drop_index("ix_quota_requests_status", table_name="quota_requests")
    op.drop_index("ix_quota_requests_user_id", table_name="quota_requests")
