from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from src.clients.reranker_client import BgeRerankerV2M3Client, RerankerClient

# ---------- ABC 규약 ----------


class TestRerankerClientABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            RerankerClient()  # type: ignore[abstract]

    def test_bge_reranker_subclasses_reranker_client(self):
        assert issubclass(BgeRerankerV2M3Client, RerankerClient)

    def test_classvar_defaults(self):
        assert BgeRerankerV2M3Client.MAX_LENGTH == 512
        assert BgeRerankerV2M3Client.DEFAULT_BATCH_SIZE == 16


# ---------- sigmoid (모델 없이) ----------


class TestSigmoid:
    """``_sigmoid``는 staticmethod라 모델 없이 직접 호출 가능."""

    def test_zero_input(self):
        result = BgeRerankerV2M3Client._sigmoid(np.array([0.0], dtype=np.float32))
        assert pytest.approx(float(result[0]), abs=1e-6) == 0.5

    def test_large_positive_approaches_one(self):
        result = BgeRerankerV2M3Client._sigmoid(np.array([20.0], dtype=np.float32))
        assert float(result[0]) > 0.999

    def test_large_negative_approaches_zero(self):
        result = BgeRerankerV2M3Client._sigmoid(np.array([-20.0], dtype=np.float32))
        assert float(result[0]) < 0.001

    def test_monotonic(self):
        xs = np.array([-3.0, -1.0, 0.0, 1.0, 3.0], dtype=np.float32)
        ys = BgeRerankerV2M3Client._sigmoid(xs)
        assert all(ys[i] < ys[i + 1] for i in range(len(ys) - 1))


# ---------- score_pairs 동작 (실모델 우회) ----------


class TestScorePairsLogic:
    """``score_pairs``의 배치 분할·sigmoid·순서 보존을 spy로 검증."""

    @staticmethod
    def _make_client(batch_size: int = 16) -> BgeRerankerV2M3Client:
        """ONNX/tokenizer 로드를 우회하고 ``_score_batch``만 spy로 갈아끼운 클라이언트."""
        client = BgeRerankerV2M3Client.__new__(BgeRerankerV2M3Client)
        client._batch_size = batch_size
        client._max_length = 512
        return client

    def test_empty_passages_returns_empty_list(self):
        client = self._make_client()
        result = asyncio.run(client.score_pairs("query", []))
        assert result == []

    def test_scores_aligned_with_input_order(self):
        client = self._make_client()
        # logit 4개 → sigmoid 적용 후 단조 증가 (-3, -1, 1, 3은 약 0.047, 0.27, 0.73, 0.95)
        client._score_batch = lambda q, p: np.array(  # type: ignore[method-assign]
            [-3.0, -1.0, 1.0, 3.0], dtype=np.float32
        )
        passages = ["p0", "p1", "p2", "p3"]
        result = asyncio.run(client.score_pairs("q", passages))
        assert len(result) == 4
        # 점수가 단조 증가 — 입력 순서가 출력 순서로 보존됨을 의미
        assert all(result[i] < result[i + 1] for i in range(3))
        assert pytest.approx(result[2], abs=1e-4) == 0.7311
        assert pytest.approx(result[3], abs=1e-4) == 0.9526

    def test_scores_in_zero_one_range(self):
        client = self._make_client()
        client._score_batch = lambda q, p: np.array(  # type: ignore[method-assign]
            [-100.0, 0.0, 100.0], dtype=np.float32
        )
        result = asyncio.run(client.score_pairs("q", ["a", "b", "c"]))
        assert all(0.0 <= s <= 1.0 for s in result)

    def test_batches_split_at_batch_size(self):
        client = self._make_client(batch_size=4)
        batch_sizes: list[int] = []

        def spy(query: str, passages: list[str]) -> np.ndarray:
            batch_sizes.append(len(passages))
            return np.zeros(len(passages), dtype=np.float32)

        client._score_batch = spy  # type: ignore[method-assign]
        # 10개 passage → batch_size=4면 [4, 4, 2]
        passages = [f"p{i}" for i in range(10)]
        result = asyncio.run(client.score_pairs("q", passages))
        assert batch_sizes == [4, 4, 2]
        assert len(result) == 10

    def test_single_batch_when_under_batch_size(self):
        client = self._make_client(batch_size=16)
        batch_count = 0

        def spy(query: str, passages: list[str]) -> np.ndarray:
            nonlocal batch_count
            batch_count += 1
            return np.zeros(len(passages), dtype=np.float32)

        client._score_batch = spy  # type: ignore[method-assign]
        asyncio.run(client.score_pairs("q", ["p0", "p1", "p2"]))
        assert batch_count == 1

    def test_query_passed_to_every_batch(self):
        client = self._make_client(batch_size=2)
        seen_queries: list[str] = []

        def spy(query: str, passages: list[str]) -> np.ndarray:
            seen_queries.append(query)
            return np.zeros(len(passages), dtype=np.float32)

        client._score_batch = spy  # type: ignore[method-assign]
        asyncio.run(client.score_pairs("쿼리", ["p0", "p1", "p2", "p3", "p4"]))
        # 5 passages, batch_size=2 → 3개 배치, 모두 같은 query
        assert len(seen_queries) == 3
        assert all(q == "쿼리" for q in seen_queries)


# ---------- BgeRerankerV2M3Client (실모델 통합) ----------

MODEL_ROOT = Path("./models/bge-reranker-v2-m3-onnx-int8")
SKIP_REASON = "Reranker ONNX 모델이 없음 — scripts/setup_bge_reranker.py 먼저 실행"


def _model_available() -> bool:
    return (MODEL_ROOT / "model.onnx").exists() and (MODEL_ROOT / "tokenizer.json").exists()


@pytest.fixture(scope="module")
def reranker_client() -> Any:
    if not _model_available():
        pytest.skip(SKIP_REASON)
    return BgeRerankerV2M3Client()


@pytest.mark.requires_model
@pytest.mark.skipif(not _model_available(), reason=SKIP_REASON)
class TestBgeRerankerV2M3ClientReal:
    def test_relevant_scores_higher_than_irrelevant(self, reranker_client: BgeRerankerV2M3Client):
        scores = asyncio.run(
            reranker_client.score_pairs(
                "SOC 사업 경제성",
                [
                    "SOC 투자의 B/C 비율은 1.32로 측정됐다.",
                    "오늘 점심 메뉴는 김치찌개입니다.",
                ],
            )
        )
        assert len(scores) == 2
        assert (
            scores[0] > scores[1]
        ), f"관련 passage 점수가 무관 passage보다 낮음: {scores[0]} <= {scores[1]}"

    def test_scores_in_zero_one_range(self, reranker_client: BgeRerankerV2M3Client):
        scores = asyncio.run(
            reranker_client.score_pairs(
                "노인 일자리",
                ["정부의 고령자 일자리 사업.", "강아지 산책 요령."],
            )
        )
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_empty_passages_returns_empty(self, reranker_client: BgeRerankerV2M3Client):
        scores = asyncio.run(reranker_client.score_pairs("query", []))
        assert scores == []

    def test_batch_split_consistency(self, reranker_client: BgeRerankerV2M3Client):
        """같은 (query, passage) 쌍은 배치 위치와 무관하게 같은 점수를 줘야 한다.

        cross-encoder는 쌍 단위 추론이라 batch padding이 점수에 영향을 줘선 안 됨.
        """
        passages = [
            "SOC 투자의 B/C 비율은 1.32로 측정됐다.",
            "오늘 점심 메뉴는 김치찌개입니다.",
            "예비타당성조사 면제 기준이 강화됐다.",
        ]
        single = [asyncio.run(reranker_client.score_pairs("SOC 경제성", [p]))[0] for p in passages]
        batched = asyncio.run(reranker_client.score_pairs("SOC 경제성", passages))
        for s, b in zip(single, batched, strict=True):
            # padding이 모델 출력에 미세 영향을 주므로 0.01 tolerance
            assert abs(s - b) < 0.01, f"batch padding으로 점수 변동: {s} vs {b}"
