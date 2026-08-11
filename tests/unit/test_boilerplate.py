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


class TestWebNoise:
    """영문 자료가 근거의 78%가 되면서 드러난 유형들(2026-08-12 실전 런).

    실제로 보고서가 트위터 공유 URL 청크를 6번 인용했다 - 각주가 공유 버튼을
    가리키는 상태였다.
    """

    def test_공유_링크만_늘어놓은_청크는_링크_목록이다(self) -> None:
        content = (
            "[https://twitter.com/intent/tweet?text=Short-Form%20Video%20Trends]"
            "(https://twitter.com/intent/tweet?text=x) "
            "[https://www.facebook.com/dialog/send?app_id=1405866](https://facebook.com/x)"
        )
        assert boilerplate_kind(content) == "링크 목록"

    def test_태그_목록도_링크_목록이다(self) -> None:
        content = " ".join(
            f"[태그{i}](https://hrcopinion.co.kr/archives/tag/x{i})" for i in range(8)
        )
        assert boilerplate_kind(content) == "링크 목록"

    def test_본문에_링크가_섞인_것은_남긴다(self) -> None:
        body = (
            "글로벌 숏폼 시장은 2026년까지 연평균 25.6% 성장할 전망이다. "
            "자세한 내용은 [보고서](https://example.com/report)에서 확인할 수 있다. " * 3
        )
        assert boilerplate_kind(body) is None

    def test_증권사_면책조항을_잡는다(self) -> None:
        content = (
            "동 자료의 금융투자분석사는 자료 작성일 현재 동 자료상에 언급된 기업들의 "
            "금융투자상품 및 권리를 보유하고 있지 않습니다."
        )
        assert boilerplate_kind(content) == "면책조항"

    def test_영문_면책조항을_잡는다(self) -> None:
        content = "All expressions of opinions are subject to change without notice."
        assert boilerplate_kind(content) == "면책조항"

    def test_영업_문구를_잡는다(self) -> None:
        content = "Ready for a Next Level of Enterprise Growth? We reply within 24 hours."
        assert boilerplate_kind(content) == "영업 문구"

    def test_문서_배지를_잡는다(self) -> None:
        content = "### reStructuredText ``` .. image:: https://zenodo.org/badge/DOI/10.5281/x"
        assert boilerplate_kind(content) == "문서 메타"

    def test_흔한_단어_하나로_긴_본문을_버리지_않는다(self) -> None:
        """실측 오탐: '누적 조회수는 500억 회를 초과'가 사이트 메뉴로 잡혔다."""
        body = (
            "매일 약 6.2억 명의 사용자가 숏폼 드라마를 소비하며, 히트작의 누적 조회수는 "
            "500억 회를 초과하는 만큼 이미 중국 콘텐츠 시장의 중심부에 있다. " * 6
        )
        assert boilerplate_kind(body) is None

    def test_짧은_페이지_가구는_그대로_잡는다(self) -> None:
        assert (
            boilerplate_kind("## 글자 크기 설정 * 가 보통 * 가 크게 - 기사 공유") == "사이트 메뉴"
        )
