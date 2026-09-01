"""절 하나의 근거 되짚기 — 본문 번호 → 청크 매핑을 푸는 단일 지점.

라우터(조회 화면)와 PM 검증이 같은 규칙을 봐야 화면의 경고와 검증 리포트가 어긋나지
않는다. 표기 문법이 바뀌었을 때 한쪽만 고쳐 오분류가 나는 사고를 막으려고 여기 모은다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.services.qa.alignment import marker_numbers_in_order


def _as_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def marker_chunk_ids(
    content: str,
    source_ids: list[Any],
    meta: dict[str, Any] | None,
    *,
    renumbered: bool = False,
) -> tuple[dict[int, list[UUID]], bool]:
    """본문 근거 번호 → 청크 id. (매핑, 청크까지 되짚을 수 있는가)

    세 갈래다.
    1. renumber가 남긴 meta.citation_chunks가 있으면 그게 정확한 답이다(전역 번호는
       자료 단위라 본문만 봐서는 청크를 못 고른다).
    2. 조립 전 절은 본문 번호가 아직 검색 풀 인덱스라, 등장 순서 ↔ source_ids 위치
       대응으로 푼다(작성기 _extract_cited_ids와 같은 규약).
    3. 조립을 지났는데 기록이 없는 옛 절은 되짚을 수 없다. 재작성 때 못 푼 마커가
       지워지고 같은 자료의 서로 다른 번호가 합쳐져, 위치 대응이 더는 성립하지 않는다.
       엉뚱한 원문을 근거라고 보여주느니 대응을 포기한다(빈 매핑 + traceable=False).
    """
    recorded = (meta or {}).get("citation_chunks")
    if isinstance(recorded, dict) and recorded:
        out: dict[int, list[UUID]] = {}
        for key, ids in recorded.items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            parsed = [u for u in (_as_uuid(i) for i in ids) if u is not None]
            if parsed:
                out[number] = parsed
        if out:
            return out, True
    if renumbered:
        return {}, False

    numbers = marker_numbers_in_order(content or "")
    cited = [u for u in (_as_uuid(s) for s in (source_ids or [])) if u is not None]
    positional = {n: [cited[i]] for i, n in enumerate(numbers) if i < len(cited)}
    return positional, bool(positional)
