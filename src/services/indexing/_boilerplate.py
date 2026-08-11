"""근거로 쓸모없는 청크 판별 — 사이트 메뉴·그림 껍데기·목차·참고문헌·깨진 인코딩.

인용된 청크 729건 전수 측정(2026-08-11)에서 나온 비율:

    그림 플레이스홀더만 남은 껍데기  6.4%
    사이트 템플릿(도메인 반복 라인)  5.9%
    깨진 인코딩                      3.8%
    사이트 메뉴 키워드               2.5%
    문서 목차                        2.2%
    참고문헌 목록                    1.9%

이것들이 검색 상위를 먹으면 두 번 손해다 — top_k 예산을 잠식하고, 모델이 인용까지 한다
(실제로 각주가 "메뉴 선택"을 가리키는 사례가 나왔다). 게다가 이런 청크는 도메인 키워드를
그대로 담고 있어 주제 질의와 유사도가 높게 나온다. 무작위 노이즈가 아니라 상위로 뽑히는
노이즈다.

**버리지 않고 표시만 한다.** 판정이 틀렸을 때 되돌릴 수 있어야 하고, 원문 대조 화면은
모델이 실제로 받은 것을 그대로 보여줘야 한다. 검색에서만 제외한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from hashlib import md5
from typing import Any

# 사이트 내비게이션·공유 위젯 — 본문에는 나올 이유가 없는 어휘.
_NAV_TOKENS: tuple[str, ...] = (
    "메뉴 선택",
    "주요메뉴",
    "이전 기사",
    "다음 기사",
    "글자크기",
    "조회수",
    "공유 열기",
    "바로가기",
    "sns-",
    "화면 프린트",
    "링크 공유",
    "통합검색",
    "인기 검색어",
    "즐겨찾기",
    "목록보기",
    "로그인 하시겠습니까",
)
# 파서가 이미지 자리에 남기는 표식 — 이것만 남은 청크는 근거가 아니다.
_PICTURE_RE = re.compile(r"\*?\*?==>\s*picture[^<]*<==\*?\*?")
# 목차의 점선("서론 ...... 3")과 참고문헌 표기.
_TOC_RE = re.compile(r"\.{5,}")
_REF_RE = re.compile(r"Retrieved from|doi\.org|pp\.\s?\d+", re.IGNORECASE)
# 인코딩이 깨진 자리(U+FFFD) — "글로벌�반도체" 처럼 본문 자체가 못 읽는 상태.
_REPLACEMENT_CHAR = "�"

# 그림 표식을 걷어낸 뒤 이만큼도 안 남으면 껍데기로 본다.
_PICTURE_SHELL_CHARS = 200
_MIN_MOJIBAKE = 3
_MIN_TOC_DOTS = 3
_MIN_REF_HITS = 3


def boilerplate_kind(content: str) -> str | None:
    """근거로 못 쓰는 청크면 그 이유를, 아니면 None.

    이유를 문자열로 돌려주는 이유: 화면·리포트에서 "왜 빠졌는지"를 보여줘야 사람이
    판정을 검증할 수 있다. 참/거짓만 남기면 오판을 영영 못 찾는다.
    """
    if not content or not content.strip():
        return "빈 청크"
    if content.count(_REPLACEMENT_CHAR) >= _MIN_MOJIBAKE:
        return "깨진 인코딩"
    if any(token in content for token in _NAV_TOKENS):
        return "사이트 메뉴"
    if (
        _PICTURE_RE.search(content)
        and len(_PICTURE_RE.sub("", content).strip()) < _PICTURE_SHELL_CHARS
    ):
        return "그림 껍데기"
    if len(_TOC_RE.findall(content)) >= _MIN_TOC_DOTS:
        return "문서 목차"
    if len(_REF_RE.findall(content)) >= _MIN_REF_HITS:
        return "참고문헌 목록"
    return None


def is_boilerplate(content: str) -> bool:
    return boilerplate_kind(content) is not None


async def excluded_metadata(
    session: Any, project_id: Any, chunks: Sequence[Any]
) -> list[dict[str, Any]]:
    """색인 직전 청크들의 metadata에 배제 이유를 실어 돌려준다.

    두 가지를 본다 - 보일러플레이트(내용만 보면 판정 가능)와 내용 중복(프로젝트 안에 이미
    같은 본문이 있는가). 중복은 같은 문서를 웹 수집과 파일 업로드로 두 번 넣으면 생긴다.
    실측에서 한 보고서가 54청크 + 55청크 두 벌로 들어가 프롬프트에 [2][3]처럼 같은 내용이
    두 번 실렸다.

    이 배선이 없으면 필터는 백필한 옛 자료에만 걸리고 새로 수집한 자료는 그대로 통과한다.
    """
    from sqlalchemy import text

    seen: set[str] = set(
        (
            await session.execute(
                text("SELECT DISTINCT md5(content) FROM chunks WHERE project_id = :p"),
                {"p": str(project_id)},
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        meta = dict(getattr(chunk, "metadata", None) or {})
        kind = boilerplate_kind(chunk.content or "")
        if not kind:
            digest = md5((chunk.content or "").encode("utf-8")).hexdigest()
            if digest in seen:
                kind = "내용 중복"
            else:
                seen.add(digest)
        if kind:
            meta["excluded"] = kind
        out.append(meta)
    return out
