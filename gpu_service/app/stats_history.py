"""큐·GPU 상태의 시계열 이력 — 대시보드가 "지금"이 아니라 "흐름"을 보게 한다.

/health는 스냅샷이라 "거절이 언제 몰렸는지", "대기가 어떻게 자랐는지"를 답하지
못한다. 외부 저장소 없이 프로세스 메모리에 링버퍼로 든다 — 5초 간격 1시간이면
720칸이라 수 MB도 안 되고, 컨테이너 재시작에 날아가는 것도 감수한다(장기 보관은
netdata 몫이고, 이 버퍼는 대시보드의 최근 1시간 그래프 전용이다).
"""

from __future__ import annotations

from collections import deque
from typing import Any

# 5초 간격 × 720칸 = 1시간. 임베딩 배치(4.5초)보다 간격이 짧으면 같은 작업을
# 두 번 세는 것처럼 보일 뿐 정보가 늘지 않는다.
SAMPLE_INTERVAL_S = 5.0
_MAX_SAMPLES = 720


class StatsHistory:
    """고정 길이 링버퍼. 응답은 필드별 병렬 배열 — 720칸이어도 키 반복이 없어
    JSON이 절반 이하로 준다(터널 너머 폰에서 읽는 걸 생각하면 공짜가 아니다).
    """

    def __init__(self, maxlen: int = _MAX_SAMPLES) -> None:
        self._samples: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def record(
        self,
        *,
        t: float,
        queue: dict[str, Any],
        gpu: dict[str, Any] | None,
    ) -> None:
        self._samples.append(
            {
                "t": int(t),
                "in_flight": queue["in_flight"],
                "estimated_wait_s": queue["estimated_wait_s"],
                "avg_task_s": queue["avg_task_s"],
                # 누적값을 그대로 둔다 - 구간 증가분(rate)은 읽는 쪽이 이웃 칸과의
                # 차로 만든다. 여기서 미리 빼면 샘플 유실 때 증가분이 사라진다.
                "completed_total": queue["completed_total"],
                "rejected_total": queue["rejected_total"],
                "gpu_util_pct": None if gpu is None else gpu["utilization_pct"],
                "vram_used_mib": None if gpu is None else gpu["memory_used_mib"],
                "temperature_c": None if gpu is None else gpu["temperature_c"],
                "power_w": None if gpu is None else gpu["power_w"],
            }
        )

    def series(self) -> dict[str, Any]:
        fields = (
            "t",
            "in_flight",
            "estimated_wait_s",
            "avg_task_s",
            "completed_total",
            "rejected_total",
            "gpu_util_pct",
            "vram_used_mib",
            "temperature_c",
            "power_w",
        )
        out: dict[str, Any] = {"interval_s": SAMPLE_INTERVAL_S}
        for field in fields:
            out[field] = [s[field] for s in self._samples]
        return out
