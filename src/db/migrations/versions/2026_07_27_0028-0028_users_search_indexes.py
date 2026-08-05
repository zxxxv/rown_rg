"""add pgroonga indexes on users(email, name) and btree index on users(created_at)

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_users_email_pgroonga ON users USING pgroonga (email)")
    op.execute("CREATE INDEX ix_users_name_pgroonga ON users USING pgroonga (name)")
    op.create_index("ix_users_created_at", "users", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_users_created_at", table_name="users")
    op.execute("DROP INDEX ix_users_name_pgroonga")
    op.execute("DROP INDEX ix_users_email_pgroonga")
