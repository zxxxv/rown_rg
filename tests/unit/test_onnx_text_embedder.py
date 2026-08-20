"""임베딩 알맹이의 순수 함수들 — 모델 없이 검증 가능한 부분.

여기 있는 것들이 틀리면 **에러 없이 벡터만 나빠진다**. 정규화가 어긋나면 코사인
유사도가 의미를 잃고, 배치 분할이 어긋나면 OOM이 나거나 순서가 바뀐다. 모델을
띄우지 않고도 잡을 수 있는 것들이라 여기서 잡는다.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.clients.onnx_text_embedder import (
    DIMENSION,
    MAX_INPUT_CHARS,
    chars_budget_for_bytes,
    clamp_input,
    l2_normalize,
    make_dynamic_batches,
)


class TestL2Normalize:
    def test_rows_become_unit_length(self):
        x = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
        out = l2_normalize(x)
        assert np.allclose(np.linalg.norm(out, axis=1), 1.0)

    def test_direction_is_preserved(self):
        """크기만 바뀌고 방향은 그대로여야 한다 - 방향이 바뀌면 검색 결과가 바뀐다."""
        x = np.array([[3.0, 4.0]], dtype=np.float32)
        out = l2_normalize(x)
        assert out[0][0] / out[0][1] == pytest.approx(3.0 / 4.0)

    def test_zero_vector_does_not_produce_nan(self):
        """0-벡터는 이론상 안 나오지만, 나왔을 때 NaN이 색인에 들어가면
        그 벡터와의 비교가 전부 오염된다."""
        out = l2_normalize(np.zeros((1, 4), dtype=np.float32))
        assert np.all(np.isfinite(out))


class TestClampInput:
    def test_long_text_is_cut(self):
        assert len(clamp_input(["가" * 10_000])[0]) == MAX_INPUT_CHARS

    def test_short_text_untouched(self):
        assert clamp_input(["짧은 글"]) == ["짧은 글"]

    def test_order_preserved(self):
        texts = ["a", "나" * 9_999, "c"]
        out = clamp_input(texts)
        assert out[0] == "a" and out[2] == "c"


class TestDynamicBatches:
    def test_all_texts_appear_exactly_once_in_order(self):
        """배치를 나눠도 순서와 개수가 보존돼야 한다 - 여기가 어긋나면 청크와
        벡터가 어긋난 채로 색인에 들어간다."""
        texts = [f"{i}" * 10 for i in range(20)]
        batches = make_dynamic_batches(texts, 55)
        flat = [t for b in batches for t in b]
        assert flat == texts

    def test_respects_char_budget(self):
        texts = ["a" * 30] * 10
        for b in make_dynamic_batches(texts, 100):
            assert sum(len(t) for t in b) <= 100 or len(b) == 1

    def test_oversized_single_text_becomes_its_own_batch(self):
        """버리지 않는다 - 상한을 넘겨도 통과시킨다(clamp_input이 앞서 자른다)."""
        batches = make_dynamic_batches(["짧다", "긴" * 500, "짧다"], 100)
        assert [t for b in batches for t in b] == ["짧다", "긴" * 500, "짧다"]

    def test_empty_input(self):
        assert make_dynamic_batches([], 100) == []


class TestCharsBudget:
    @pytest.mark.parametrize(
        ("gb", "expected"),
        [(64, 128_000), (32, 128_000), (30, 128_000), (16, 32_000), (14, 32_000), (8, 16_000)],
    )
    def test_tiers(self, gb: int, expected: int):
        assert chars_budget_for_bytes(gb * 1024**3) == expected

    def test_monotonic(self):
        """메모리가 늘었는데 상한이 줄면 안 된다."""
        vals = [chars_budget_for_bytes(g * 1024**3) for g in (4, 8, 14, 20, 30, 64)]
        assert vals == sorted(vals)

    def test_tiny_host_gets_conservative_value(self):
        """CI 컨테이너나 8GB VRAM처럼 작은 예산에서 크게 잡으면 OOM이 난다."""
        assert chars_budget_for_bytes(2 * 1024**3) == 16_000


def test_dimension_matches_model_contract():
    """1024는 bge-m3 가중치가 학습된 차원이라 바꿀 수 없다. 원격 응답 검증도 이 값을 쓴다."""
    assert DIMENSION == 1024
