"""add partial unique indexes on project_sources for upsert

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # library / upload 소스에 대해 ON CONFLICT 가능한 UPSERT 키 부여.
    # web_search는 두 컬럼 모두 NULL이므로 부분 인덱스 대상에서 제외돼 매번 INSERT.
    op.create_index(
        "uq_project_sources_project_library",
        "project_sources",
        ["project_id", "library_node_id"],
        unique=True,
        postgresql_where="library_node_id IS NOT NULL",
    )
    op.create_index(
        "uq_project_sources_project_upload",
        "project_sources",
        ["project_id", "upload_path"],
        unique=True,
        postgresql_where="upload_path IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_project_sources_project_upload", table_name="project_sources")
    op.drop_index("uq_project_sources_project_library", table_name="project_sources")
