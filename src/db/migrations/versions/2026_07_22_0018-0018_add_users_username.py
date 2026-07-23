"""add users.username (username-or-email login)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=50), nullable=True))
    op.create_unique_constraint("users_username_key", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("users_username_key", "users", type_="unique")
    op.drop_column("users", "username")
