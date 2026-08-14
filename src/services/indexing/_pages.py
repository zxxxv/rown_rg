"""청크 → PDF 페이지 매핑 - 파서가 심은 페이지 경계 마커를 읽고 지운다.

PDF 파서(clients/parser/pdf.py)는 페이지 사이에 PAGE_BREAK_MARKER를 심는다.
여기서 마커를 걷어낸 본문으로 청킹하고, 각 청크의 시작 위치가 몇 쪽인지 되짚어
metadata["page"]로 남긴다. 화면의 "PDF 원본 p.N 열기"가 이 값을 쓴다.

페이지는 판정이 아니라 위치 안내라 근사면 충분하다 - 청킹이 원문을 변형(헤더를
metadata로 이동, 소청크 병합)하므로 정확 일치 대신 청크 첫 줄로 위치를 찾고,
못 찾으면 페이지를 달지 않는다(틀린 쪽을 가리키는 것보다 낫다).
"""

from __future__ import annotations

from bisect import bisect_right

from src.clients.parser.base import PAGE_BREAK_MARKER

# 위치 탐색에 쓸 청크 첫 줄의 길이 - 짧으면 오매칭, 길면 변형에 취약.
_PROBE_CHARS = 80
_MIN_PROBE_CHARS = 8


def strip_page_markers(markdown: str) -> tuple[str, list[int]]:
    """마커를 제거한 본문과 각 페이지의 시작 오프셋(제거 후 기준)을 돌려준다.

    반환의 page_starts[i]는 (i+1)쪽이 시작하는 문자 오프셋. 마커가 없는 문서
    (웹·HWPX·DOCX 등)는 [0] 하나만 돌아오고, 호출자는 길이 1 이하를 "페이지를
    모른다"로 읽어야 한다.
    """
    parts = markdown.split(PAGE_BREAK_MARKER)
    if len(parts) <= 1:
        return markdown, [0]
    starts: list[int] = []
    pos = 0
    for part in parts:
        starts.append(pos)
        pos += len(part)
    return "".join(parts), starts


def assign_chunk_pages(
    chunk_texts: list[str], clean_md: str, page_starts: list[int]
) -> list[int | None]:
    """각 청크의 시작 페이지(1-기반). 위치를 못 찾은 청크는 None.

    커서를 앞으로만 움직여 같은 문장이 반복돼도 문서 순서를 지킨다. 병합·변형으로
    커서 뒤에서 못 찾으면 문서 전체에서 한 번 더 찾는다(청킹 병합은 앞쪽 내용을
    뒤 청크에 붙이는 방향이라 드물지만 실재한다).
    """
    if len(page_starts) <= 1:
        return [None] * len(chunk_texts)
    pages: list[int | None] = []
    cursor = 0
    for text in chunk_texts:
        probe = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:_PROBE_CHARS]
        if len(probe) < _MIN_PROBE_CHARS:
            pages.append(None)
            continue
        at = clean_md.find(probe, cursor)
        if at < 0:
            at = clean_md.find(probe)
        if at < 0:
            pages.append(None)
            continue
        # 한 칸이라도 전진해야 같은 소제목이 반복되는 문서에서 순서가 지켜진다.
        cursor = at + 1
        # page_starts[k] <= at 인 k의 개수 = 1-기반 페이지 번호
        pages.append(bisect_right(page_starts, at))
    return pages
