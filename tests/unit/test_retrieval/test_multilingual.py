"""번역 질의 병합 — 외국어 자료가 있을 때만 켜지는지, 순위가 제대로 합쳐지는지."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from src.services.retrieval._multilingual import (
    make_gated_translator,
    make_query_translator,
    merge_rankings,
)
from src.services.retrieval.base import SearchHit


def _hit(score: float = 1.0) -> SearchHit:
    return SearchHit(
        chunk_id=uuid4(),
        source_id=uuid4(),
        content="본문",
        score=score,
        metadata={},
        chunk_index=0,
        score_source="semantic",
    )


class _FakeRow:
    def __init__(self, foreign: int, total: int) -> None:
        self._v = (foreign, total)

    def __getitem__(self, i: int) -> int:
        return self._v[i]


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    def __init__(self, foreign: int, total: int) -> None:
        self._row = _FakeRow(foreign, total)

    async def execute(self, *_a: Any, **_k: Any) -> _FakeResult:
        return _FakeResult(self._row)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None


def _maker(foreign: int, total: int):
    return lambda: _FakeSession(foreign, total)


class _FakeLLM:
    """complete() 호출 횟수를 세는 최소 LLM."""

    def __init__(self, reply: str = "semiconductor market size forecast") -> None:
        self.calls: list[str] = []
        self._reply = reply

    async def complete(self, request: Any) -> Any:
        self.calls.append(request.messages[0].content)

        class _R:
            content = self._reply

        return _R()


@pytest.mark.asyncio
async def test_외국어_자료가_없으면_번역하지_않는다() -> None:
    llm = _FakeLLM()
    translate = make_gated_translator(
        _maker(0, 100), uuid4(), model="m", min_foreign_ratio=0.05, client=llm
    )
    assert await translate("차세대 반도체 시장 규모", "주제") == ""
    assert llm.calls == []


@pytest.mark.asyncio
async def test_외국어_자료가_섞이면_자동으로_켜진다() -> None:
    llm = _FakeLLM()
    translate = make_gated_translator(
        _maker(12, 100), uuid4(), model="m", min_foreign_ratio=0.05, client=llm
    )
    assert (
        await translate("차세대 반도체 시장 규모", "주제") == "semiconductor market size forecast"
    )
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_판정은_한_번만_한다() -> None:
    llm = _FakeLLM()
    translate = make_gated_translator(
        _maker(0, 100), uuid4(), model="m", min_foreign_ratio=0.05, client=llm
    )
    await translate("가", "주제")
    await translate("나", "주제")
    assert llm.calls == []


@pytest.mark.asyncio
async def test_문맥을_함께_보낸다() -> None:
    llm = _FakeLLM()
    translate = make_query_translator(model="m", client=llm)
    await translate("시장 규모", "차세대 AI 반도체 - 시장 규모 - 성장률 전망")
    assert "차세대 AI 반도체" in llm.calls[0]
    assert "시장 규모" in llm.calls[0]


@pytest.mark.asyncio
async def test_같은_질의는_한_번만_번역한다() -> None:
    llm = _FakeLLM()
    translate = make_query_translator(model="m", client=llm)
    await translate("시장 규모", "문맥")
    await translate("시장 규모", "문맥")
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_이미_영문인_질의는_번역하지_않는다() -> None:
    llm = _FakeLLM()
    translate = make_query_translator(model="m", client=llm)
    assert await translate("semiconductor market", "context") == ""
    assert llm.calls == []


@pytest.mark.asyncio
async def test_번역_실패는_빈_문자열로_삼킨다() -> None:
    class _Boom:
        async def complete(self, _r: Any) -> Any:
            raise RuntimeError("provider down")

    translate = make_query_translator(model="m", client=_Boom())
    assert await translate("시장 규모", "문맥") == ""


def test_순위를_합치고_같은_청크는_한_번만_남긴다() -> None:
    shared = _hit()
    primary = [shared, _hit()]
    secondary = [_hit(), shared]
    merged = merge_rankings(primary, secondary)
    assert len(merged) == 3
    assert merged.count(shared) == 1


def test_양쪽에_다_있는_청크가_위로_올라온다() -> None:
    both = _hit()
    only_primary = _hit()
    merged = merge_rankings([only_primary, both], [both])
    assert merged[0] is both
