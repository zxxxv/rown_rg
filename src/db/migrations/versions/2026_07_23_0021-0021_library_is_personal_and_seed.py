"""add library_nodes.is_personal + seed company shared folders

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-23

라이브러리를 개인(나만)/회사 공유(조직 전체) 2탑레벨로 가르기 위한 `is_personal`
플래그를 추가하고, 회사 공유의 기본 폴더 골격(공통 자료·분석 자료)을 시딩한다.
시드 폴더는 고정 UUID로 멱등하게(재적용·환경 일관성) 만든다.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 회사 공유 기본 폴더 — (id, parent_id, name). 고정 UUID로 멱등 시딩.
_ROOT_COMMON = "a0000001-0000-4000-a000-000000000001"
_ROOT_ANALYSIS = "a0000002-0000-4000-a000-000000000001"

_TOP_FOLDERS: list[tuple[str, str]] = [
    (_ROOT_COMMON, "공통 자료"),
    (_ROOT_ANALYSIS, "분석 자료"),
]

_CHILD_FOLDERS: list[tuple[str, str, str]] = [
    # (id, parent_id, name)
    ("a0000001-0000-4000-a000-000000000011", _ROOT_COMMON, "정부 통계"),
    ("a0000001-0000-4000-a000-000000000012", _ROOT_COMMON, "정부 보고서"),
    ("a0000001-0000-4000-a000-000000000013", _ROOT_COMMON, "법률·규제"),
    ("a0000001-0000-4000-a000-000000000014", _ROOT_COMMON, "학술 논문"),
    ("a0000001-0000-4000-a000-000000000015", _ROOT_COMMON, "언론·보도"),
    ("a0000002-0000-4000-a000-000000000011", _ROOT_ANALYSIS, "STEEP 분석"),
    ("a0000002-0000-4000-a000-000000000012", _ROOT_ANALYSIS, "SWOT 분석"),
    ("a0000002-0000-4000-a000-000000000013", _ROOT_ANALYSIS, "비용편익 모델"),
]

_ALL_SEED_IDS = (
    [fid for fid, _ in _TOP_FOLDERS] + [fid for fid, _, _ in _CHILD_FOLDERS]
)


def _seed_table() -> sa.Table:
    return sa.table(
        "library_nodes",
        sa.column("id", sa.UUID()),
        sa.column("parent_id", sa.UUID()),
        sa.column("name", sa.String()),
        sa.column("type", sa.String()),
        sa.column("is_personal", sa.Boolean()),
    )


def upgrade() -> None:
    op.add_column(
        "library_nodes",
        sa.Column(
            "is_personal",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index("ix_library_nodes_is_personal", "library_nodes", ["is_personal"])

    lib = _seed_table()
    # 부모(top) 먼저 — 자기참조 FK 순서 보장(별도 문장으로 나눠 executemany여도 안전).
    op.bulk_insert(
        lib,
        [
            {"id": fid, "parent_id": None, "name": name, "type": "folder", "is_personal": False}
            for fid, name in _TOP_FOLDERS
        ],
    )
    op.bulk_insert(
        lib,
        [
            {"id": fid, "parent_id": pid, "name": name, "type": "folder", "is_personal": False}
            for fid, pid, name in _CHILD_FOLDERS
        ],
    )


def downgrade() -> None:
    ids = ", ".join(f"'{i}'" for i in _ALL_SEED_IDS)
    op.execute(f"DELETE FROM library_nodes WHERE id IN ({ids})")  # noqa: S608 (고정 UUID)
    op.drop_index("ix_library_nodes_is_personal", table_name="library_nodes")
    op.drop_column("library_nodes", "is_personal")
