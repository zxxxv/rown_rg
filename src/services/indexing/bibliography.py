"""문서 머리에서 서지 조각(발행기관·호수)을 보수적으로 뽑는다.

출처 표기가 "제조 수출기업의 RE100 대응 실태와 과제.pdf"처럼 파일명 그대로 나가던
것의 처방(2026-08-27 사용자 지시: "제목, 발행기관, 발행년도, 몇호" 꼴로). 발행연도는
published_year 추출기가 이미 있고, 여기는 나머지 둘을 맡는다.

원칙은 표제 추출과 같다 — **미상이면 안 단다.** 발행기관을 넓은 패턴으로 긁으면
"재무부문"·"연구원 출신" 같은 산문이 기관이 되므로, ①줄 전체가 기관명 꼴이거나
②"발행처:" 라벨이 붙은 것만 받는다. 접미사는 오인 여지가 적은 것만 허용한다
(부·처·청·원 단독은 산문과 겹쳐 뺀다).
"""

from __future__ import annotations

import re

# "2024년 17호"·"2024년 제17호" — 정기간행물 호수 표기. 연도가 함께 있어야 받는다
# (맨 "제3호"는 법령 조항·문서 절 번호와 겹친다).
_ISSUE_RE = re.compile(r"(?:19|20)\d{2}년\s*제?\s*\d{1,3}\s*호")
# 기관명으로만 이루어진 줄 — 안전한 접미사 화이트리스트.
_PUBLISHER_SUFFIX = "연구원|연구소|협회|공단|공사|진흥원|재단|학회|중앙회|위원회|연구센터"
_PUBLISHER_LINE_RE = re.compile(rf"^[가-힣A-Za-z·()\s]{{1,26}}(?:{_PUBLISHER_SUFFIX})$")
_PUBLISHER_LABEL_RE = re.compile(r"(?:발행처|발행기관)\**\s*[:：]?\s*([가-힣A-Za-z·()\s]{2,30})")
# 마크다운 장식 걷기 — 표제 추출과 같은 이유(머리 기호가 줄 판정을 흐린다).
_MD_NOISE_RE = re.compile(r"^[#>*\-\s]+|[*_`]+")

_HEAD_CHARS = 3000
_HEAD_LINES = 60
_TAIL_CHARS = 2000


def _trim_publisher(raw: str) -> str:
    r"""캡처 꼬리 정리 - 문자류가 [가-힣A-Za-z·()\s]라 "(Tel" 같은 절단 조각이 딸려온다."""
    text = re.sub(r"\s*\([A-Za-z\s]*$", "", raw)  # 닫히지 않은 영문 괄호 조각
    return text.strip(" ·,-")


def extract_bibliography(markdown: str) -> dict[str, str]:
    """문서 머리에서 {publisher, issue_label} — 못 찾은 키는 아예 없다."""
    text = markdown or ""
    head = text[:_HEAD_CHARS]
    # 판권지(발행처)는 문서 끝에 있는 경우도 많다 - 꼬리도 본다.
    tail = text[-_TAIL_CHARS:] if len(text) > _HEAD_CHARS else ""
    out: dict[str, str] = {}

    m = _ISSUE_RE.search(head) or _ISSUE_RE.search(tail)
    if m:
        out["issue_label"] = re.sub(r"\s+", " ", m.group()).replace("년 제", "년 ").strip()

    for segment in (head, tail):
        if not segment or "publisher" in out:
            break
        label = _PUBLISHER_LABEL_RE.search(segment)
        if label:
            out["publisher"] = _trim_publisher(label.group(1))
            break
        for raw in segment.splitlines()[:_HEAD_LINES]:
            line = _MD_NOISE_RE.sub("", raw).strip()
            if _PUBLISHER_LINE_RE.match(line):
                out["publisher"] = line
                break
    return out


async def backfill(project_id=None) -> int:
    """기존 자료의 서지 조각 백필 — 첫 청크들 본문에서 재추출한다(재파싱 없이).

    추출은 색인 시점에 붙었으므로 옛 자료에는 비어 있다. 문서 머리는 첫 두 청크에
    담겨 있어 그걸로 충분하다. published_year는 첫 청크 메타에서 승계한다.
    갱신한 자료 수를 돌려준다.
    """
    from sqlalchemy import select

    from src.db.models.chunk import Chunk
    from src.db.models.project_source import ProjectSource
    from src.db.session import async_session_maker

    updated = 0
    async with async_session_maker() as session:
        # 웹 자료는 뺀다 - 기사 본문 속 타기관 인용("KB경영연구소에 따르면")을
        # 발행기관으로 오인한다(2026-08-27 실측). 웹은 URL이 서지 역할을 한다.
        q = select(ProjectSource).where(ProjectSource.source_type.in_(("upload", "library")))
        if project_id is not None:
            q = q.where(ProjectSource.project_id == project_id)
        sources = (await session.execute(q)).scalars().all()
        for src in sources:
            meta = dict(src.metadata_ or {})
            rows = (
                await session.execute(
                    select(Chunk.content, Chunk.metadata_)
                    .where(Chunk.source_id == src.id)
                    .order_by(Chunk.chunk_index)
                    .limit(2)
                )
            ).all()
            tail_rows = (
                await session.execute(
                    select(Chunk.content, Chunk.metadata_)
                    .where(Chunk.source_id == src.id)
                    .order_by(Chunk.chunk_index.desc())
                    .limit(2)
                )
            ).all()
            if not rows:
                continue
            head = (chr(10) * 2).join(content or "" for content, _m in rows)
            tail = (chr(10) * 2).join(content or "" for content, _m in reversed(tail_rows))
            # 머리+꼬리를 한 본문처럼 - 판권지(발행기관)는 문서 끝에 있는 경우가 많다.
            # 사이를 띄워 head가 _HEAD_CHARS를 넘게 해 tail이 꼬리 스캔 구간에 들게 한다.
            pad = chr(10) + " " * max(0, 3100 - len(head)) + chr(10)
            bib = extract_bibliography(head + pad + tail)
            # 빠진 키만 채운다 - 색인이 단 것을 백필이 덮지 않는다.
            bib = {k: v for k, v in bib.items() if not meta.get(k)}
            year = (rows[0][1] or {}).get("published_year")
            if isinstance(year, int) and "published_year" not in meta:
                bib["published_year"] = year  # type: ignore[assignment]
            if not bib:
                continue
            src.metadata_ = {**meta, **bib}
            updated += 1
        await session.commit()
    return updated


if __name__ == "__main__":  # python -m src.services.indexing.bibliography
    import asyncio

    print(asyncio.run(backfill()))
