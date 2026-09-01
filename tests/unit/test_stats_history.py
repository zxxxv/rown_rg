"""이력 링버퍼 — 대시보드 그래프의 원천이 깨지지 않게 지킨다.

지키려는 것: (1) 필드별 병렬 배열의 길이가 항상 서로 같다 — 하나라도 어긋나면
프론트가 zip하다가 그래프가 통째로 어긋난다. (2) 용량을 넘으면 오래된 것부터
버린다. (3) GPU를 못 읽은 칸은 None으로 남는다 — 0으로 채우면 "한가함"과
"못 읽음"이 구분되지 않는다.
"""

from __future__ import annotations

from gpu_service.app.stats_history import StatsHistory

_QUEUE = {
    "in_flight": 2,
    "estimated_wait_s": 1.5,
    "avg_task_s": 3.0,
    "completed_total": 10,
    "rejected_total": 1,
    "max_wait_s": 55.0,
    "last_wait_ms": 12.0,
}

_GPU = {
    "utilization_pct": 40,
    "memory_used_mib": 5000,
    "memory_total_mib": 8192,
    "temperature_c": 50,
    "power_w": 100.0,
    "name": "RTX 3060 Ti",
}


class TestSeries:
    def test_parallel_arrays_have_equal_length(self):
        h = StatsHistory()
        for i in range(3):
            h.record(t=1000.0 + i, queue=_QUEUE, gpu=_GPU)
        s = h.series()
        lengths = {k: len(v) for k, v in s.items() if isinstance(v, list)}
        assert set(lengths.values()) == {3}

    def test_values_land_in_named_fields(self):
        h = StatsHistory()
        h.record(t=1000.0, queue=_QUEUE, gpu=_GPU)
        s = h.series()
        assert s["t"] == [1000]
        assert s["estimated_wait_s"] == [1.5]
        assert s["gpu_util_pct"] == [40]
        assert s["completed_total"] == [10]

    def test_missing_gpu_is_none_not_zero(self):
        h = StatsHistory()
        h.record(t=1000.0, queue=_QUEUE, gpu=None)
        s = h.series()
        assert s["gpu_util_pct"] == [None]
        assert s["vram_used_mib"] == [None]
        # 큐 수치는 GPU를 못 읽어도 그대로 남는다
        assert s["in_flight"] == [2]


class TestRingBuffer:
    def test_drops_oldest_beyond_capacity(self):
        h = StatsHistory(maxlen=2)
        for i in range(3):
            h.record(t=float(i), queue=_QUEUE, gpu=None)
        assert h.series()["t"] == [1, 2]
