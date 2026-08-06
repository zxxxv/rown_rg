"""0032 library: 공통 자료→공유 자료 rename + 분석 자료·시드 하위폴더 삭제(평평한 공유 공간)

회사 공유를 '공유 자료' 단일 평면 공간으로 재편한다 — 사용자들이 자유롭게 폴더·파일을 넣는
협업 공간. 분석 자료와 시드 하위폴더는 제거한다. (권한: 추가=worker+, 삭제=생성자/관리자 —
기존 라우터 규칙 그대로.)

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROOT_COMMON = "a0000001-0000-4000-a000-000000000001"
_ROOT_ANALYSIS = "a0000002-0000-4000-a000-000000000001"

# 0021이 시딩한 하위폴더 — downgrade에서 재생성한다.
_COMMON_SUBS = [
    ("a0000001-0000-4000-a000-000000000011", "정부 통계"),
    ("a0000001-0000-4000-a000-000000000012", "정부 보고서"),
    ("a0000001-0000-4000-a000-000000000013", "법률·규제"),
    ("a0000001-0000-4000-a000-000000000014", "학술 논문"),
    ("a0000001-0000-4000-a000-000000000015", "언론·보도"),
]
_ANALYSIS_SUBS = [
    ("a0000002-0000-4000-a000-000000000011", "STEEP 분석"),
    ("a0000002-0000-4000-a000-000000000012", "SWOT 분석"),
    ("a0000002-0000-4000-a000-000000000013", "비용편익 모델"),
]


def upgrade() -> None:
    # 공통 자료 → 공유 자료 (평평한 팀 공용 공간)
    op.execute(f"UPDATE library_nodes SET name='공유 자료' WHERE id='{_ROOT_COMMON}'")  # noqa: S608
    # 공유 자료의 시드 하위폴더 제거 — 사용자가 자유롭게 넣도록 비운다(FK CASCADE로 하위 포함)
    op.execute(f"DELETE FROM library_nodes WHERE parent_id='{_ROOT_COMMON}'")  # noqa: S608
    # 분석 자료 폴더 통째 삭제(CASCADE로 하위 포함)
    op.execute(f"DELETE FROM library_nodes WHERE id='{_ROOT_ANALYSIS}'")  # noqa: S608


def _reinsert(node_id: str, parent_id: str | None, name: str) -> None:
    parent = "NULL" if parent_id is None else f"'{parent_id}'"
    op.execute(  # noqa: S608 (고정 UUID·리터럴)
        "INSERT INTO library_nodes (id, parent_id, name, type, is_personal) "
        f"VALUES ('{node_id}', {parent}, '{name}', 'folder', false) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(f"UPDATE library_nodes SET name='공통 자료' WHERE id='{_ROOT_COMMON}'")  # noqa: S608
    for node_id, name in _COMMON_SUBS:
        _reinsert(node_id, _ROOT_COMMON, name)
    _reinsert(_ROOT_ANALYSIS, None, "분석 자료")
    for node_id, name in _ANALYSIS_SUBS:
        _reinsert(node_id, _ROOT_ANALYSIS, name)
