from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from scripts.setup_bge_m3 import (
    KOREAN_SAMPLES,
    MAX_ACCURACY_LOSS,
    MAX_PARA_MS,
    MAX_SIZE_MB,
    MIN_SPEEDUP,
    compute_stats,
    cosine_similarity,
    directory_size_mb,
    evaluate_thresholds,
    file_size_mb,
    mean_cosine_similarity,
)

OK = dict(size_mb=500.0, accuracy_loss=0.01, speedup=3.0, ms_per_para=200.0)


def _kw(**overrides) -> dict:
    return {**OK, **overrides}


class TestEvaluateThresholdsPositive:
    @pytest.mark.parametrize(
        "case",
        [
            _kw(),
            _kw(
                size_mb=MAX_SIZE_MB,
                accuracy_loss=MAX_ACCURACY_LOSS,
                speedup=MIN_SPEEDUP,
                ms_per_para=MAX_PARA_MS,
            ),
            _kw(size_mb=0.1, accuracy_loss=0.0, speedup=10.0, ms_per_para=1.0),
            _kw(size_mb=699.99, ms_per_para=299.99),
            _kw(accuracy_loss=MAX_ACCURACY_LOSS - 1e-6),
        ],
    )
    def test_all_within_thresholds_passes(self, case: dict):
        result = evaluate_thresholds(**case)
        assert result.ok is True
        assert result.failures == []


class TestEvaluateThresholdsNegative:
    @pytest.mark.parametrize(
        "case,expected_substring",
        [
            (_kw(size_mb=MAX_SIZE_MB + 0.1), "size_mb"),
            (_kw(accuracy_loss=MAX_ACCURACY_LOSS + 1e-4), "accuracy_loss"),
            (_kw(speedup=MIN_SPEEDUP - 0.01), "speedup"),
            (_kw(ms_per_para=MAX_PARA_MS + 0.1), "ms_per_para"),
        ],
    )
    def test_single_threshold_violation(self, case: dict, expected_substring: str):
        result = evaluate_thresholds(**case)
        assert result.ok is False
        assert len(result.failures) == 1
        assert expected_substring in result.failures[0]

    def test_multiple_threshold_violations_reported(self):
        result = evaluate_thresholds(
            size_mb=MAX_SIZE_MB + 100,
            accuracy_loss=MAX_ACCURACY_LOSS + 0.1,
            speedup=MIN_SPEEDUP - 1.0,
            ms_per_para=MAX_PARA_MS + 100,
        )
        assert result.ok is False
        assert len(result.failures) == 4

    def test_boundary_is_inclusive(self):
        """경계값(==임계값)은 통과해야 함. 비교 연산자 변형(>↔>=) 회귀를 잡는다."""
        result = evaluate_thresholds(
            size_mb=MAX_SIZE_MB,
            accuracy_loss=MAX_ACCURACY_LOSS,
            speedup=MIN_SPEEDUP,
            ms_per_para=MAX_PARA_MS,
        )
        assert result.ok is True


class TestCosineSimilarity:
    """per-pair (1D vectors)."""

    @pytest.mark.parametrize(
        "vec_a,vec_b,expected",
        [
            ([1, 0, 0], [1, 0, 0], 1.0),
            ([1, 0, 0], [2, 0, 0], 1.0),  # 크기 다름, 방향 같음
            ([1, 0, 0], [-1, 0, 0], -1.0),
            ([1, 0, 0], [0, 1, 0], 0.0),
            ([1, 1, 0], [1, 0, 0], pytest.approx(0.7071, abs=1e-3)),
        ],
    )
    def test_cosine_similarity(self, vec_a: list[float], vec_b: list[float], expected: float):
        result = cosine_similarity(np.array(vec_a), np.array(vec_b))
        assert result == pytest.approx(expected, abs=1e-3)

    @pytest.mark.parametrize(
        "vec_a,vec_b",
        [
            ([0.0, 0.0], [1.0, 0.0]),
            ([1.0, 0.0], [0.0, 0.0]),
            ([0.0, 0.0], [0.0, 0.0]),
        ],
    )
    def test_zero_vector_does_not_explode(self, vec_a: list[float], vec_b: list[float]):
        result = cosine_similarity(np.array(vec_a), np.array(vec_b))
        assert np.isfinite(result)


class TestMeanCosineSimilarity:
    """batch (N, D) — 행별 평균."""

    @pytest.mark.parametrize(
        "matrix_a,matrix_b,expected",
        [
            (np.array([[1.0, 0.0]]), np.array([[1.0, 0.0]]), 1.0),
            (np.array([[1.0, 0.0]]), np.array([[-1.0, 0.0]]), -1.0),
            (np.array([[1.0, 0.0]]), np.array([[0.0, 1.0]]), 0.0),
            (np.array([[3.0, 4.0]]), np.array([[6.0, 8.0]]), 1.0),
            (
                np.array([[1.0, 0.0], [0.0, 1.0]]),
                np.array([[1.0, 0.0], [0.0, 1.0]]),
                1.0,
            ),
            (
                np.array([[1.0, 0.0], [1.0, 0.0]]),
                np.array([[1.0, 0.0], [0.0, 1.0]]),
                0.5,
            ),
        ],
    )
    def test_positive(self, matrix_a: np.ndarray, matrix_b: np.ndarray, expected: float):
        assert mean_cosine_similarity(matrix_a, matrix_b) == pytest.approx(expected, abs=1e-6)


class TestSamplesSentences:
    """KOREAN_SAMPLES 그리드 — 한국어 평가 샘플 보장."""

    def test_count_matches_spec(self):
        assert len(KOREAN_SAMPLES) == 10

    def test_samples_are_korean(self):
        for s in KOREAN_SAMPLES:
            assert any("가" <= c <= "힯" for c in s), s

    def test_samples_diverse_lengths(self):
        lengths = [len(s) for s in KOREAN_SAMPLES]
        assert min(lengths) >= 10
        assert max(lengths) <= 50
        assert len(set(lengths)) >= 5


class TestBenchmarkStats:
    """compute_stats — mean/median/p95."""

    @pytest.mark.parametrize(
        "times,expected_mean,expected_median,expected_p95",
        [
            ([100.0], 100.0, 100.0, 100.0),
            ([100, 200, 300, 400, 500], 300.0, 300.0, 480.0),
            (
                [100, 100, 100, 100, 10000],
                2080.0,
                100.0,
                pytest.approx(8020.0, abs=20),
            ),
        ],
    )
    def test_benchmark_stats(
        self,
        times: list[float],
        expected_mean: float,
        expected_median: float,
        expected_p95: float,
    ):
        stats = compute_stats(list(map(float, times)))
        assert stats["mean"] == pytest.approx(expected_mean, abs=1.0)
        assert stats["median"] == pytest.approx(expected_median, abs=1.0)
        assert stats["p95"] == pytest.approx(expected_p95, abs=50.0)


class TestDirectorySize:
    def test_empty_directory_is_zero(self, tmp_path: Path):
        assert directory_size_mb(tmp_path) == 0.0

    def test_nonexistent_path_is_zero(self, tmp_path: Path):
        assert directory_size_mb(tmp_path / "missing") == 0.0

    def test_single_file_counted(self, tmp_path: Path):
        (tmp_path / "a.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))
        assert directory_size_mb(tmp_path) == pytest.approx(2.0, abs=0.01)

    def test_nested_files_summed(self, tmp_path: Path):
        (tmp_path / "a.bin").write_bytes(b"\x00" * (1 * 1024 * 1024))
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.bin").write_bytes(b"\x00" * (3 * 1024 * 1024))
        assert directory_size_mb(tmp_path) == pytest.approx(4.0, abs=0.01)

    def test_file_size_mb_missing_returns_zero(self, tmp_path: Path):
        assert file_size_mb(tmp_path / "missing.onnx") == 0.0
