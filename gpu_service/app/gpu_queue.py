"""GPU 접근 큐 — 동시 실행 제한 + 타임아웃 날 요청만 미리 거절.

**기다리는 쪽이 거의 항상 이긴다.** 리랭킹 1건이 GPU에서 1.5초, CPU 폴백이 140초다.
30초를 기다렸다가 GPU로 처리하면 31.5초, 즉시 거절해서 CPU로 보내면 140초다.
대기가 138초를 넘지 않는 한 기다리는 편이 빠르다. 그래서 이 큐는 **웬만하면 줄을
세운다** — 큐 길이가 길다는 이유만으로 거절하지 않는다.

**거절이 이기는 경우는 하나뿐이다: 어차피 타임아웃 날 요청.** 클라이언트가 60초에
포기하도록 설정돼 있는데 예상 대기가 70초라면, 그 요청은 60초를 버린 **뒤에** CPU로
가서 총 200초가 된다. 즉시 돌려보내면 140초다. 이때만 429가 이득이다.

그래서 판단 기준은 큐 길이가 아니라 **예상 대기 시간**이다. 실제 처리 시간을
지수이동평균으로 재서 ``대기 중 요청 수 × 평균 처리 시간``으로 추정한다. 고정
상한을 쓰면 리랭킹(1.5초)과 임베딩(4.5초)의 차이를 반영하지 못한다 - 같은 큐 길이
8이라도 12초와 36초로 세 배 다르다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# 처리 시간 지수이동평균 계수. 작을수록 과거를 오래 기억한다. 0.2면 최근 대여섯 건이
# 지배적이라 리랭킹↔임베딩 전환을 몇 건 안에 따라잡는다.
_EWMA_ALPHA = 0.2
# 아직 한 건도 처리하지 않았을 때 쓸 초기 추정치. 임베딩 배치(4.5초) 쪽에 맞춘
# 보수적인 값 — 처음부터 과소평가해서 통과시키는 것보다 낫다.
_INITIAL_TASK_S = 4.5
# 메모리 방어용 절대 상한. 예상 대기 기준을 통과하더라도 이보다 쌓이지는 않게 한다.
# 정상 운영에서는 닿을 일이 없고, 클라이언트가 폭주할 때의 안전망이다.
_ABSOLUTE_CAP = 256


class GpuBusy(Exception):
    """예상 대기가 클라이언트가 감당할 시간을 넘음 — 호출부가 429로 변환한다."""

    def __init__(self, in_flight: int, estimated_wait_s: float, limit_s: float) -> None:
        super().__init__(
            f"예상 대기 {estimated_wait_s:.1f}초가 상한 {limit_s:.0f}초를 넘습니다 "
            f"(대기 {in_flight}건). 지금 기다리면 타임아웃 후 폴백이라 더 느립니다."
        )
        self.in_flight = in_flight
        self.estimated_wait_s = estimated_wait_s
        self.limit_s = limit_s


class GpuQueue:
    """리랭킹과 임베딩이 **함께** 쓰는 큐.

    둘이 같은 카드를 쓰므로 각자 세마포어를 가지면 동시에 돌아 활성화 텐서가 두 배로
    필요해진다. 하나를 공유해 한 번에 하나만 GPU에 올린다.
    """

    def __init__(self, concurrency: int, max_wait_s: float) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._concurrency = max(1, concurrency)
        # 0이면 거절하지 않고 무조건 기다린다(절대 상한만 적용).
        self._max_wait_s = max_wait_s
        self._in_flight = 0
        self._avg_task_s = _INITIAL_TASK_S
        self._completed = 0
        self._last_wait_ms: float = 0.0
        self._rejected = 0

    @property
    def in_flight(self) -> int:
        """대기 중 + 실행 중 요청 수."""
        return self._in_flight

    @property
    def last_wait_ms(self) -> float:
        return self._last_wait_ms

    @property
    def rejected(self) -> int:
        """기동 이후 429로 돌려보낸 횟수. 0이 아니면 용량을 다시 봐야 한다."""
        return self._rejected

    def estimated_wait_s(self) -> float:
        """지금 새 요청이 들어오면 얼마나 기다릴지.

        앞에 선 요청 수를 동시 실행 수로 나눈 뒤 평균 처리 시간을 곱한다. 실행 중인
        것도 포함되므로 약간 보수적이다(이미 절반쯤 처리됐을 수 있다) — 과소평가해서
        타임아웃을 맞는 것보다 낫다.
        """
        ahead = max(0, self._in_flight)
        return (ahead / self._concurrency) * self._avg_task_s

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """GPU 차례를 기다린다. 어차피 타임아웃 날 상황이면 ``GpuBusy``를 즉시 올린다.

        확인과 카운터 증가 사이에 await가 없다 - 그 사이에 다른 코루틴이 끼어들면
        상한을 넘겨 통과시키게 된다. 단일 이벤트 루프에서 이 순서를 지키는 것이
        락 없이 정확성을 얻는 방법이다.
        """
        wait_s = self.estimated_wait_s()
        too_long = self._max_wait_s > 0 and wait_s > self._max_wait_s
        if too_long or self._in_flight >= _ABSOLUTE_CAP:
            self._rejected += 1
            raise GpuBusy(self._in_flight, wait_s, self._max_wait_s)

        self._in_flight += 1
        t0 = time.perf_counter()
        try:
            async with self._sem:
                t_start = time.perf_counter()
                self._last_wait_ms = round((t_start - t0) * 1000, 1)
                try:
                    yield
                finally:
                    # 실제 처리 시간을 학습한다 - 리랭킹과 임베딩은 세 배 차이가 나고
                    # 그 차이가 예상 대기의 정확도를 좌우한다.
                    took = time.perf_counter() - t_start
                    self._avg_task_s = (
                        _EWMA_ALPHA * took + (1 - _EWMA_ALPHA) * self._avg_task_s
                    )
                    self._completed += 1
        finally:
            self._in_flight -= 1

    def snapshot(self) -> dict[str, object]:
        """/health 노출용 — 큐가 얼마나 밀려 있는지 밖에서 보이게 한다."""
        return {
            "in_flight": self._in_flight,
            "estimated_wait_s": round(self.estimated_wait_s(), 2),
            "max_wait_s": self._max_wait_s,
            "avg_task_s": round(self._avg_task_s, 3),
            "completed_total": self._completed,
            "last_wait_ms": self._last_wait_ms,
            "rejected_total": self._rejected,
        }
