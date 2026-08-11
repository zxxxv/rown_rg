"""근거로 못 쓰는 청크 판별 — 실측에서 나온 유형을 그대로 시험한다."""

from __future__ import annotations

from typing import Any

import pytest

from src.services.indexing._boilerplate import (
    boilerplate_kind,
    excluded_metadata,
    is_boilerplate,
)


def test_그림_표식만_남은_청크는_껍데기다() -> None:
    content = "**==> picture 3 <==**\n\n그림 3. 반도체 공급망"
    assert boilerplate_kind(content) == "그림 껍데기"


def test_그림_표식이_있어도_본문이_충분하면_남긴다() -> None:
    body = "글로벌 AI 반도체 시장은 2028년 1,590억 달러로 성장할 전망이다. " * 6
    assert boilerplate_kind(f"==> picture 1 <==\n{body}") is None


def test_사이트_메뉴는_본문이_아니다() -> None:
    assert boilerplate_kind("메뉴 선택 | 통합검색 | 즐겨찾기 | 목록보기") == "사이트 메뉴"


def test_깨진_인코딩을_잡는다() -> None:
    assert boilerplate_kind("글로벌�반도체�시장�전망") == "깨진 인코딩"


def test_문서_목차의_점선을_잡는다() -> None:
    toc = "서론 .......... 3\n본론 .......... 12\n결론 .......... 45"
    assert boilerplate_kind(toc) == "문서 목차"


def test_참고문헌_목록을_잡는다() -> None:
    refs = (
        "Kim, J. (2024). AI chips. pp. 12-30. Retrieved from https://example.com\n"
        "Lee, S. (2023). Foundry. doi.org/10.1000/x pp. 4-9\n"
        "Park, H. (2022). Memory. Retrieved from https://example.org pp. 1-8"
    )
    assert boilerplate_kind(refs) == "참고문헌 목록"


def test_빈_청크를_잡는다() -> None:
    assert boilerplate_kind("   \n  ") == "빈 청크"


def test_정상_본문은_통과한다() -> None:
    body = "세계 AI 반도체 시장은 향후 5년간 연평균 24% 성장하여 2028년 1,590억 달러 전망이다."
    assert boilerplate_kind(body) is None
    assert is_boilerplate(body) is False


class _FakeChunk:
    def __init__(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        self.content = content
        self.metadata = metadata or {}


class _FakeResult:
    def __init__(self, hashes: list[str]) -> None:
        self._hashes = hashes

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[str]:
        return self._hashes


class _FakeSession:
    """excluded_metadata가 쓰는 건 '기존 본문 해시 조회' 한 번뿐이다."""

    def __init__(self, hashes: list[str] | None = None) -> None:
        self._hashes = hashes or []

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._hashes)


@pytest.mark.asyncio
async def test_색인_시점에_보일러플레이트를_표시한다() -> None:
    chunks = [
        _FakeChunk("메뉴 선택 | 통합검색"),
        _FakeChunk("정상 본문이 충분히 길게 이어진다." * 3),
    ]
    metas = await excluded_metadata(_FakeSession(), "pid", chunks)
    assert metas[0]["excluded"] == "사이트 메뉴"
    assert "excluded" not in metas[1]


@pytest.mark.asyncio
async def test_이미_있는_본문과_같으면_중복으로_표시한다() -> None:
    from hashlib import md5

    body = "같은 문서를 웹 수집과 파일 업로드로 두 번 넣으면 이렇게 된다."
    existing = md5(body.encode("utf-8")).hexdigest()
    metas = await excluded_metadata(_FakeSession([existing]), "pid", [_FakeChunk(body)])
    assert metas[0]["excluded"] == "내용 중복"


@pytest.mark.asyncio
async def test_같은_배치_안의_중복도_뒤엣것만_표시한다() -> None:
    body = "한 자료 안에서 같은 문단이 두 번 잘려 나오는 경우도 있다."
    metas = await excluded_metadata(_FakeSession(), "pid", [_FakeChunk(body), _FakeChunk(body)])
    assert "excluded" not in metas[0]
    assert metas[1]["excluded"] == "내용 중복"


@pytest.mark.asyncio
async def test_기존_metadata를_보존한다() -> None:
    chunk = _FakeChunk("메뉴 선택", {"header_path": ["1장"]})
    metas = await excluded_metadata(_FakeSession(), "pid", [chunk])
    assert metas[0]["header_path"] == ["1장"]
    assert metas[0]["excluded"] == "사이트 메뉴"
