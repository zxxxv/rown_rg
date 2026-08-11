"""본문 출처·인용 표기 규약 — 작성·검증·조립·API가 같은 문법을 쓰도록 한곳에 모은다.

두 표기를 구분한다(2026-08-11 확정):

- ``(출처 n)`` — 자료를 **참고해 재작성**한 문장에 붙인다. 본문에 흘리지 않고, 웹 프리뷰는
  블록 단위로 모아 보여주며 납품물(HWPX) 본문에서는 걷어낸다. 출처는 참고문헌 최종장으로만
  밝힌다.
- ``[n]`` — 원문을 **그대로 옮긴** 직접 인용에만 붙인다. 본문 문장 끝에 그대로 남는다.

전에는 둘 다 ``[n]``이었다. 그러다 보니 독자가 본문 문장을 원문에서 검색했다 못 찾고
표기 자체를 불신했다 — 참고해 쓴 글에 직접 인용 표기가 붙어 있었기 때문이다.

번호의 의미는 문맥에 따라 다르다(절-로컬 검색 풀 인덱스 → 조립 시 전역 출처 번호).
이 모듈은 **문법만** 책임지고 번호 체계는 건드리지 않는다.
"""

from __future__ import annotations

import re

# 마커 하나 = 앞 가로공백(개행 제외 — 줄이 합쳐지지 않게) + 출처형 또는 인용형.
# 출처형은 "(출처 3)"과 "(출처 3, 7)"을 함께 받는다. 인용형의 (?!\()는 마크다운 링크
# "[1](url)"의 라벨을 마커로 착각하지 않으려는 것이다.
MARK_RE = re.compile(
    r"(?P<space>[^\S\n]*)"
    r"(?:\(출처\s*(?P<source>\d+(?:\s*,\s*\d+)*)\s*\)"
    r"|\[(?P<quote>\d+)\](?!\())"
)

# 출처형만 — 본문에서 걷어낼 때 쓴다(직접 인용 [n]은 남겨야 하므로 따로 둔다).
_SOURCE_MARK_RE = re.compile(r"[^\S\n]*\(출처\s*(?P<nums>\d+(?:\s*,\s*\d+)*)\s*\)")

_NUM_SPLIT_RE = re.compile(r"\s*,\s*")


def _mark_numbers(match: re.Match[str]) -> list[int]:
    """마커 하나가 담은 번호들 — 출처형은 여럿, 인용형은 하나."""
    source = match.group("source")
    if source is not None:
        return [int(n) for n in _NUM_SPLIT_RE.split(source)]
    return [int(match.group("quote"))]


def numbers_in_order(content: str) -> list[int]:
    """본문에 등장한 번호를 첫 등장 순서로, 중복 없이.

    출처형·인용형을 가리지 않는다 — 둘 다 "몇 번 근거를 썼는가"를 뜻하고, 작성기가
    cited_chunk_ids를 이 순서로 저장하는 규약(candidates._extract_cited_ids)의 근거다.
    """
    seen: list[int] = []
    for match in MARK_RE.finditer(content):
        for n in _mark_numbers(match):
            if n not in seen:
                seen.append(n)
    return seen


def renumber(content: str, local_to_global: dict[int, int]) -> str:
    """마커의 번호를 매핑대로 갈아끼운다. 표기 형태(출처형/인용형)는 그대로 둔다.

    매핑에 없는 번호는 지운다 — 로컬 번호를 그대로 남기면 참고문헌의 다른 자료를
    가리켜 오독을 부른다. 출처형에서 일부만 매핑되면 매핑된 것만 남기고, 하나도
    없으면 마커째 앞 공백까지 지운다.
    """

    def _sub(match: re.Match[str]) -> str:
        mapped = [local_to_global[n] for n in _mark_numbers(match) if n in local_to_global]
        if not mapped:
            return ""
        space = match.group("space")
        if match.group("source") is not None:
            return f"{space}(출처 {', '.join(str(g) for g in mapped)})"
        return f"{space}[{mapped[0]}]"

    return MARK_RE.sub(_sub, content)


def strip_source_marks(content: str) -> str:
    """참고 표기 ``(출처 n)``만 앞 공백까지 걷어낸다. 직접 인용 ``[n]``은 남긴다."""
    return _SOURCE_MARK_RE.sub("", content)


def source_numbers(content: str) -> list[int]:
    """참고 표기가 가리킨 번호들 — 첫 등장 순서, 중복 없이.

    웹 프리뷰가 블록마다 우상단에 모아 보일 출처 목록이다.
    """
    seen: list[int] = []
    for match in _SOURCE_MARK_RE.finditer(content):
        for n in (int(x) for x in _NUM_SPLIT_RE.split(match.group("nums"))):
            if n not in seen:
                seen.append(n)
    return seen


def to_source_mark(content: str) -> str:
    """옛 본문의 ``[n]``을 모두 ``(출처 n)``으로 바꾼다(마이그레이션 전용).

    표기를 나누기 전에는 ``[n]``이 예외 없이 "참고했다"는 뜻이었다. 그대로 두면 새 규약
    아래에서 전부 직접 인용으로 읽혀 납품물 본문에 되살아난다. 마크다운 링크와
    그림·표 캡션은 건드리지 않는다.
    """
    return re.sub(r"\[(\d+)\](?!\()", lambda m: f"(출처 {m.group(1)})", content)
