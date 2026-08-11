"""0035 옛 본문의 [n]을 (출처 n)으로 — 참고 표기와 직접 인용 표기 분리

표기를 나누기 전(2026-08-11 이전) 본문의 [n]은 예외 없이 "이 자료를 참고했다"는
뜻이었다. 새 규약에서 [n]은 원문을 그대로 옮긴 직접 인용만 가리키고, 참고는
(출처 n)으로 쓴다 — 그리고 납품물(HWPX) 본문에서는 참고 표기만 걷어낸다.

옛 본문을 그대로 두면 모든 [n]이 직접 인용으로 읽혀 한글 파일 본문에 되살아난다.
그래서 저장된 본문의 [n]을 (출처 n)으로 옮긴다. 마크다운 링크 [텍스트](url)와
그림·표 캡션([그림 1-1], [표 2-1])은 건드리지 않는다.

되돌리기는 (출처 n) → [n]이다. 새 규약에서 진짜 직접 인용으로 쓴 [n]까지 함께
되돌아가지는 않지만, 그건 원래 [n]이었으니 결과는 옛 규약과 같다.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 뒤에 '('가 오지 않는 [숫자]만 — 마크다운 링크 라벨을 건드리지 않으려는 것.
# 숫자만 있는 대괄호라 '[그림 1-1]'·'[표 2-1]' 캡션은 애초에 걸리지 않는다.
_QUOTE_TO_SOURCE = r"\[([0-9]+)\](?!\()"
_SOURCE_TO_QUOTE = r"\(출처 ([0-9]+)\)"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE sections
        SET content = regexp_replace(content, '{_QUOTE_TO_SOURCE}', '(출처 \\1)', 'g')
        WHERE content ~ '{_QUOTE_TO_SOURCE}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE sections
        SET content = regexp_replace(content, '{_SOURCE_TO_QUOTE}', '[\\1]', 'g')
        WHERE content ~ '{_SOURCE_TO_QUOTE}'
        """
    )
