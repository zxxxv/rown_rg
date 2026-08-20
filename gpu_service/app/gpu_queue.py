"""GPU 접근 큐 — 동시 실행 제한 + 과부하 시 빠른 거절.

**왜 세마포어만으로는 부족한가.** 세마포어는 줄을 세우기만 하고 줄 길이를 보지
않는다. 요청이 몰리면 뒤쪽 요청은 클라이언트 타임아웃(60초)을 꼬박 기다린 뒤에야
폴백한다 - 그 60초는 아무것도 하지 않고 버려진다. 게다가 그 사이 큐는 계속 자라서
뒤로 갈수록 상황이 나빠진다.

큐가 이미 감당 못 할 만큼 길면 **즉시 429로 돌려보내는 편이 낫다**. 클라이언트는
기다리지 않고 바로 CPU 폴백으로 넘어가고, GPU는 처리 가능한 만큼만 받는다.
거절이 목적이 아니라 **빨리 실패시키는 것**이 목적이다.

상한을 정하는 기준: 리랭킹 1건이 약 1.5초, 임베딩 256건 배치가 약 4.5초다
(3060 Ti fp16 실측). 기본값 8이면 최악의 경우 대기가 8 × 4.5초 = 36초로 클라이언트
타임아웃(60초) 안에 들어온다. 이 관계가 깨지지 않게 값을 바꿀 때 같이 확인할 것.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class GpuBusy(Exception):
    """큐가 상한에 도달 — 호출부가 429로 변환한다."""

    def __init__(self, in_flight: int, limit: int) -> None:
        super().__init__(f"GPU 큐가 가득 찼습니다: {in_flight}/{limit}")
        self.in_flight = in_flight
        self.limit = limit


class GpuQueue:
    """리랭킹과 임베딩이 **함께** 쓰는 큐.

    둘이 같은 카드를 쓰므로 각자 세마포어를 가지면 동시에 돌아 활성화 텐서가 두 배로
    필요해진다. 하나를 공유해 한 번에 하나만 GPU에 올린다.
    """

    def __init__(self, concurrency: int, max_in_flight: int) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        # 0이면 상한 없음(옛 동작). 운영에서 끄고 싶을 때를 위한 탈출구다.
        self._max_in_flight = max_in_flight
        self._in_flight = 0
        self._last_wait_ms: float = 0.0
        self._rejected = 0

    @property
    def in_flight(self) -> int:
        """대기 중 + 실행 중 요청 수."""
        return self._in_flight

    @property
    def max_in_flight(self) -> int:
        return self._max_in_flight

    @property
    def last_wait_ms(self) -> float:
        """직전 요청이 큐에서 기다린 시간 — 0에 가까우면 여유롭다는 뜻."""
        return self._last_wait_ms

    @property
    def rejected(self) -> int:
        """기동 이후 429로 돌려보낸 횟수. 0이 아니면 용량을 다시 봐야 한다."""
        return self._rejected

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """GPU 차례를 기다린다. 큐가 가득 차 있으면 ``GpuBusy``를 즉시 올린다.

        상한 확인과 카운터 증가 사이에 await가 없다 - 그 사이에 다른 코루틴이 끼어들면
        상한을 넘겨 통과시키게 된다. 단일 이벤트 루프에서 이 순서를 지키는 것이
        락 없이 정확성을 얻는 방법이다.
        """
        if self._max_in_flight and self._in_flight >= self._max_in_flight:
            self._rejected += 1
            raise GpuBusy(self._in_flight, self._max_in_flight)

        self._in_flight += 1
        t0 = time.perf_counter()
        try:
            async with self._sem:
                self._last_wait_ms = round((time.perf_counter() - t0) * 1000, 1)
                yield
        finally:
            self._in_flight -= 1

    def snapshot(self) -> dict[str, object]:
        """/health 노출용 — 큐가 얼마나 밀려 있는지 밖에서 보이게 한다."""
        return {
            "in_flight": self._in_flight,
            "max_in_flight": self._max_in_flight,
            "last_wait_ms": self._last_wait_ms,
            "rejected_total": self._rejected,
        }
