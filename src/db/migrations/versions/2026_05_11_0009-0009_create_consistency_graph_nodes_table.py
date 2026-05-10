"""create consistency_graph_nodes table

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consistency_graph_nodes",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("assertion_text", sa.Text(), nullable=False),
        sa.Column(
            "assertion_type",
            sa.VARCHAR(30),
            sa.CheckConstraint(
                "assertion_type IN ('numeric', 'qualitative', 'source', 'decision')",
                name="ck_consistency_graph_nodes_assertion_type",
            ),
            nullable=False,
        ),
        sa.Column("subject", sa.VARCHAR(255), nullable=False),
        sa.Column("value", sa.VARCHAR(255), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column(
            "is_user_decision",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "asserted_in", ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("level", sa.VARCHAR(20), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_consistency_graph_nodes_project_subject",
        "consistency_graph_nodes",
        ["project_id", "subject"],
    )
    op.create_index(
        "ix_consistency_graph_nodes_is_user_decision",
        "consistency_graph_nodes",
        ["is_user_decision"],
    )
    op.create_index(
        "ix_consistency_graph_nodes_asserted_in",
        "consistency_graph_nodes",
        ["asserted_in"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_consistency_graph_nodes_assertion_type",
        "consistency_graph_nodes",
        ["assertion_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consistency_graph_nodes_assertion_type", table_name="consistency_graph_nodes"
    )
    op.drop_index(
        "ix_consistency_graph_nodes_asserted_in", table_name="consistency_graph_nodes"
    )
    op.drop_index(
        "ix_consistency_graph_nodes_is_user_decision", table_name="consistency_graph_nodes"
    )
    op.drop_index(
        "ix_consistency_graph_nodes_project_subject", table_name="consistency_graph_nodes"
    )
    op.drop_table("consistency_graph_nodes")
