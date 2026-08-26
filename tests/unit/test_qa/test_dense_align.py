"""교차언어 대목 정렬 — 어휘가 0점을 주는 구간만 임베딩으로 다시 본다.

실LLM·실GPU 없이 가짜 임베딩 클라로 계약만 고정한다: 어느 문장을 건드리는가,
문턱 아래는 그대로 두는가, 실패해도 종전 판정을 지키는가.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.config import settings
from src.services.qa.alignment import ClaimAlignment, EvidenceSpan
from src.services.qa.dense_align import refine_crosslingual

pytestmark = pytest.mark.asyncio


class _Res:
    def __init__(self, text: str, embedding: list[float]) -> None:
        self.text = text
        self.embedding = embedding


class _Client:
    """텍스트 -> 벡터 표. 표에 없으면 직교 벡터를 줘 유사도가 0이 되게 한다."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table
        self.calls = 0

    async def embed_batch(self, texts: list[str]) -> list[_Res]:
        self.calls += 1
        return [_Res(t, self.table.get(t, [0.0, 0.0, 1.0])) for t in texts]


class _Boom:
    async def embed_batch(self, texts):  # noqa: ANN001, ANN202
        raise RuntimeError("임베딩 서비스 없음")


@pytest.fixture(autouse=True)
def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    # 기본 off라(뷰 지연) 테스트에서만 켠다.
    monkeypatch.setattr(settings, "dense_align_enabled", True)


def _crosslingual(claim: str, chunk_id) -> ClaimAlignment:
    """교차언어로 판정된 주장 - 대목은 잡혔지만 comparable=False라 점수가 무의미하다."""
    return ClaimAlignment(
        claim=claim,
        numbers=[3],
        span=EvidenceSpan(
            chunk_id=chunk_id, number=3, start=0, end=5, text="…", score=0.0, comparable=False
        ),
        cited_chunk_ids=[chunk_id],
        evidence_comparable=False,
    )


class TestRefineCrosslingual:
    async def test_promotes_when_over_threshold(self) -> None:
        cid = uuid4()
        line = "Korea remains one of the most challenging markets for RE100 implementation."
        chunk = line + chr(10) + "무관한 줄이 길게 이어진다."
        claim = _crosslingual(
            "한국은 RE100 이행 관점에서 가장 까다로운 시장 중 하나로 평가된다", cid
        )
        client = _Client(
            {
                claim.claim: [1.0, 0.0, 0.0],
                line: [1.0, 0.0, 0.0],
            }
        )

        n = await refine_crosslingual([claim], {cid: chunk}, client=client)

        assert n == 1
        assert claim.status == "aligned"  # 어휘로는 원리적으로 못 넘던 자리
        assert claim.span is not None
        assert "Korea remains" in claim.span.text
        assert claim.span.dense_score is not None and claim.span.dense_score >= 0.78

    async def test_leaves_low_similarity_alone(self) -> None:
        """문턱 아래는 손대지 않는다 - 아무 대목이나 확정하면 거짓 확신을 새로 만든다."""
        cid = uuid4()
        claim = _crosslingual("전혀 다른 이야기를 하는 문장이다", cid)
        before = claim.span
        client = _Client({claim.claim: [1.0, 0.0, 0.0]})  # 대목은 직교 벡터 -> 0

        n = await refine_crosslingual(
            [claim], {cid: "Something entirely unrelated here."}, client=client
        )

        assert n == 0
        assert claim.span is before
        assert claim.status == "crosslingual"

    async def test_ignores_non_crosslingual(self) -> None:
        """한글 대 한글은 겹침이 이미 잘 한다 - 검증된 경로를 미검증으로 바꾸지 않는다."""
        cid = uuid4()
        ok = ClaimAlignment(
            claim="국내 점유율은 41%로 나타났다",
            numbers=[3],
            span=EvidenceSpan(
                chunk_id=cid, number=3, start=0, end=5, text="국내 점유율 41%", score=0.9
            ),
            cited_chunk_ids=[cid],
        )
        client = _Client({})

        n = await refine_crosslingual([ok], {cid: "국내 점유율 41%"}, client=client)

        assert n == 0
        assert client.calls == 0  # 부를 일 자체가 없다
        assert ok.status == "aligned"

    async def test_embedding_failure_is_non_fatal(self) -> None:
        """임베딩이 안 닿으면 종전 판정 그대로 - 정보가 줄 뿐 틀리지 않는다."""
        cid = uuid4()
        claim = _crosslingual("한국은 RE100 이행이 까다로운 시장으로 평가된다", cid)

        n = await refine_crosslingual([claim], {cid: "Korea is challenging."}, client=_Boom())

        assert n == 0
        assert claim.status == "crosslingual"

    async def test_disabled_flag_skips_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """기본 off - 뷰 지연을 실사용자에게 지우지 않는다(대목 벡터 색인 이관 전까지)."""
        monkeypatch.setattr(settings, "dense_align_enabled", False)
        cid = uuid4()
        claim = _crosslingual("한국은 RE100 이행이 까다로운 시장으로 평가된다", cid)
        client = _Client({})

        assert (
            await refine_crosslingual([claim], {cid: "Korea is challenging."}, client=client) == 0
        )
        assert client.calls == 0
