from __future__ import annotations

from unittest.mock import MagicMock

import psutil
import pytest

from scripts.benchmark_embedding import (
    MAX_CHARS_SWEEP,
    MAX_MEAN_MS,
    MAX_P95_MS,
    MAX_PEAK_RAM_GB,
    RSS_HARD_LIMIT_GB,
    RSS_HARD_LIMIT_RATIO,
    SWEEP_MULTIPLIERS,
    check_rss_or_abort,
    compute_stats_ms,
    estimate_500p_minutes,
    evaluate_thresholds,
    split_paragraphs,
)
from src.clients.embedding_client import BgeM3Client


class TestSplitParagraphs:
    @pytest.mark.parametrize(
        "md,expected",
        [
            ("a\n\nb\n\nc", ["a", "b", "c"]),
            ("  hello  \n\nworld  ", ["hello", "world"]),
            ("only one block", ["only one block"]),
            # 연속 빈 줄 — 한 단락 경계로 압축됨이 아니라 빈 단락이 사라져야 함
            ("a\n\n\n\n\nb", ["a", "b"]),
            ("first\n\n\n\nsecond\n\nthird", ["first", "second", "third"]),
        ],
    )
    def test_positive(self, md: str, expected: list[str]):
        assert split_paragraphs(md) == expected

    @pytest.mark.parametrize(
        "md",
        [
            "",
            "   ",
            "\n\n",
            "\n   \n\n  \n  ",
        ],
    )
    def test_empty_inputs_yield_empty_list(self, md: str):
        # 공백/빈 단락은 모두 제거되어 결과는 빈 list
        assert split_paragraphs(md) == []

    def test_paragraph_with_internal_newlines_kept(self):
        md = "line 1\nline 2\n\nblock 2"
        # 한 단락 내부의 단일 newline은 보존
        assert split_paragraphs(md) == ["line 1\nline 2", "block 2"]


class TestComputeStatsMs:
    @pytest.mark.parametrize(
        "times,expected_mean,expected_median,expected_p95",
        [
            ([100.0], 100.0, 100.0, 100.0),
            ([100, 200, 300, 400, 500], 300.0, 300.0, 480.0),
            ([100, 100, 100, 100, 10000], 2080.0, 100.0, pytest.approx(8020.0, abs=20)),
        ],
    )
    def test_positive(
        self,
        times: list[float],
        expected_mean: float,
        expected_median: float,
        expected_p95: float,
    ):
        stats = compute_stats_ms(list(map(float, times)))
        assert stats["mean"] == pytest.approx(expected_mean, abs=1.0)
        assert stats["median"] == pytest.approx(expected_median, abs=1.0)
        assert stats["p95"] == pytest.approx(expected_p95, abs=50.0)

    def test_empty_input_safe(self):
        stats = compute_stats_ms([])
        assert stats == {"mean": 0.0, "median": 0.0, "p95": 0.0}


class TestEstimate500pMinutes:
    @pytest.mark.parametrize(
        "mean_ms,expected_min,tol",
        [
            # 500p × 6단락 = 3000단락. 1ms × 3000 = 3000ms = 3s = 0.05 min.
            (1.0, 0.05, 0.01),
            # 100ms × 3000 = 300_000ms = 300s = 5 min.
            (100.0, 5.0, 0.1),
            # 600ms × 3000 = 1_800_000ms = 30 min.
            (600.0, 30.0, 0.1),
            (0.0, 0.0, 0.001),
        ],
    )
    def test_linear_scaling(self, mean_ms: float, expected_min: float, tol: float):
        assert estimate_500p_minutes(mean_ms) == pytest.approx(expected_min, abs=tol)


class TestEvaluateThresholds:
    @pytest.mark.parametrize(
        "mean_ms,p95_ms,ram_gb",
        [
            # 모두 임계 한참 아래
            (50.0, 80.0, 1.0),
            # 정확히 경계값 — 명세는 < 가 아니라 ≤ 로 통과 (inclusive)
            (MAX_MEAN_MS, MAX_P95_MS, MAX_PEAK_RAM_GB),
            # 500p 견적 정확히 30분 (== 600ms × 3000 = 30분이지만 mean 600은 mean 임계 초과)
            # 그래서 견적만 정확히 임계로 만드는 mean은 NaN — 그대신 더 작은 값.
            (100.0, 200.0, 2.0),
        ],
    )
    def test_within_thresholds_passes(self, mean_ms: float, p95_ms: float, ram_gb: float):
        ok, failures = evaluate_thresholds(mean_ms, p95_ms, ram_gb)
        assert ok is True, failures
        assert failures == []

    @pytest.mark.parametrize(
        "mean_ms,p95_ms,ram_gb,expected_substring",
        [
            (MAX_MEAN_MS + 0.1, 100.0, 1.0, "mean_ms"),
            (100.0, MAX_P95_MS + 0.1, 1.0, "p95_ms"),
            (100.0, 200.0, MAX_PEAK_RAM_GB + 0.01, "peak_ram_gb"),
            # 500p 견적 초과: mean 700ms × 3000 = 35분
            (700.0, 800.0, 1.0, "500p_minutes"),
        ],
    )
    def test_single_threshold_violation_named(
        self,
        mean_ms: float,
        p95_ms: float,
        ram_gb: float,
        expected_substring: str,
    ):
        ok, failures = evaluate_thresholds(mean_ms, p95_ms, ram_gb)
        assert ok is False
        joined = " | ".join(failures)
        assert expected_substring in joined, joined

    def test_multiple_violations_all_reported(self):
        # mean=700이면 mean 임계 + 500p 견적(35분) 동시 위반. p95·ram도 같이 위반.
        ok, failures = evaluate_thresholds(
            mean_ms=700.0,
            p95_ms=MAX_P95_MS + 100,
            peak_ram_gb=MAX_PEAK_RAM_GB + 1,
        )
        assert ok is False
        assert len(failures) == 4, failures


class TestSweepDefinition:
    """sweep은 호스트 자동값에서 배수로 파생되므로 호스트 사양과 무관하게 안전."""

    def test_sweep_size_matches_multipliers(self):
        assert len(MAX_CHARS_SWEEP) == len(SWEEP_MULTIPLIERS)

    def test_sweep_values_are_monotonic(self):
        assert list(MAX_CHARS_SWEEP) == sorted(MAX_CHARS_SWEEP)
        assert len(set(MAX_CHARS_SWEEP)) == len(MAX_CHARS_SWEEP)

    def test_sweep_max_within_client_auto_value(self):
        # 클라이언트의 host-RAM 기반 자동값을 초과하면 hard cap에 걸려 측정 자체가 실패한다.
        assert max(MAX_CHARS_SWEEP) <= BgeM3Client.auto_max_chars_per_batch()

    def test_multipliers_at_most_one(self):
        assert max(SWEEP_MULTIPLIERS) <= 1.0
        assert min(SWEEP_MULTIPLIERS) > 0


class TestRssLimitDerivation:
    def test_ratio_within_reasonable_range(self):
        # baseline 모델·텐서·캐시 마진을 합쳐 적정. 너무 크면 cutoff가 OS 보호 의미를 잃는다.
        assert 0.2 <= RSS_HARD_LIMIT_RATIO <= 0.7

    def test_limit_scales_with_host_ram(self):
        expected_gb = (psutil.virtual_memory().total / 1024**3) * RSS_HARD_LIMIT_RATIO
        assert RSS_HARD_LIMIT_GB == pytest.approx(expected_gb)


class TestRssWatchdog:
    def test_returns_rss_when_under_limit(self):
        proc = MagicMock()
        # 1 GB — hard cutoff (6 GB) 한참 아래
        proc.memory_info.return_value = MagicMock(rss=1 * 1024**3)
        assert check_rss_or_abort(proc, 1, 10) == 1 * 1024**3

    def test_raises_when_exceeding_limit(self):
        proc = MagicMock()
        # 컷오프 + 100 MB
        proc.memory_info.return_value = MagicMock(rss=int((RSS_HARD_LIMIT_GB + 0.1) * 1024**3))
        with pytest.raises(RuntimeError, match="exceeded hard cutoff"):
            check_rss_or_abort(proc, 3, 10)

    def test_message_includes_chunk_progress(self):
        proc = MagicMock()
        proc.memory_info.return_value = MagicMock(rss=int((RSS_HARD_LIMIT_GB + 1.0) * 1024**3))
        with pytest.raises(RuntimeError) as exc:
            check_rss_or_abort(proc, 7, 42)
        assert "7/42" in str(exc.value)
