"""GPU 큐 — 동시 실행 제한과 과부하 시 빠른 거절.

여기서 지키려는 것: **과부하가 조용한 지연으로 나타나지 않는다.** 세마포어만 쓰면
줄이 길어져도 아무도 모르고, 뒤쪽 요청은 클라이언트 타임아웃(60초)을 꼬박 버린 뒤
폴백한다. 상한을 넘으면 즉시 거절해서 그 60초를 아끼는 것이 목적이다.
"""

from __future__ import annotations

import asyncio

import pytest

from gpu_service.app.gpu_queue import GpuBusy, GpuQueue


class TestConcurrencyLimit:
    def test_only_one_runs_at_a_time(self):
        """리랭킹과 임베딩이 같은 카드를 쓴다 - 겹쳐 돌면 VRAM이 두 배로 필요해진다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=0)
            peak = 0
            running = 0

            async def worker():
                nonlocal peak, running
                async with q.acquire():
                    running += 1
                    peak = max(peak, running)
                    await asyncio.sleep(0.01)
                    running -= 1

            await asyncio.gather(*(worker() for _ in range(5)))
            return peak

        assert asyncio.run(scenario()) == 1

    def test_concurrency_two_allows_two(self):
        async def scenario():
            q = GpuQueue(concurrency=2, max_in_flight=0)
            peak = 0
            running = 0

            async def worker():
                nonlocal peak, running
                async with q.acquire():
                    running += 1
                    peak = max(peak, running)
                    await asyncio.sleep(0.01)
                    running -= 1

            await asyncio.gather(*(worker() for _ in range(6)))
            return peak

        assert asyncio.run(scenario()) == 2


class TestAdmissionControl:
    def test_rejects_when_full(self):
        """상한을 넘으면 기다리지 않고 즉시 GpuBusy."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=2)
            started = asyncio.Event()
            release = asyncio.Event()

            async def holder():
                async with q.acquire():
                    started.set()
                    await release.wait()

            async def waiter():
                async with q.acquire():
                    pass

            h = asyncio.create_task(holder())
            await started.wait()
            w = asyncio.create_task(waiter())  # 2번째 - 대기열에 들어간다
            await asyncio.sleep(0)  # waiter가 acquire까지 진입하게

            with pytest.raises(GpuBusy) as exc:  # 3번째 - 거절
                async with q.acquire():
                    pass
            release.set()
            await asyncio.gather(h, w)
            return exc.value

        err = asyncio.run(scenario())
        assert err.limit == 2
        assert "가득" in str(err)

    def test_zero_means_unlimited(self):
        """0은 상한 없음 - 운영에서 이 기능을 끄고 싶을 때의 탈출구."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=0)
            async with q.acquire():
                # 상한이 없으므로 안에서 또 확인해도 거절되지 않아야 한다
                assert q.in_flight == 1
            return True

        assert asyncio.run(scenario())

    def test_slot_is_released_after_rejection(self):
        """거절이 카운터를 오염시키면 그 뒤로 영원히 거절하게 된다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=1)
            started = asyncio.Event()
            release = asyncio.Event()

            async def holder():
                async with q.acquire():
                    started.set()
                    await release.wait()

            h = asyncio.create_task(holder())
            await started.wait()
            for _ in range(3):
                with pytest.raises(GpuBusy):
                    async with q.acquire():
                        pass
            release.set()
            await h
            # 홀더가 끝났으니 다시 받아야 한다
            async with q.acquire():
                pass
            return q.in_flight, q.rejected

        in_flight, rejected = asyncio.run(scenario())
        assert in_flight == 0, "카운터가 새고 있다"
        assert rejected == 3

    def test_counter_returns_to_zero_on_exception(self):
        """작업이 예외로 죽어도 슬롯은 반납돼야 한다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=2)
            with pytest.raises(ValueError):
                async with q.acquire():
                    raise ValueError("작업 실패")
            return q.in_flight

        assert asyncio.run(scenario()) == 0


class TestSnapshot:
    def test_exposes_state_for_health(self):
        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=4)
            async with q.acquire():
                pass
            return q.snapshot()

        snap = asyncio.run(scenario())
        assert snap["in_flight"] == 0
        assert snap["max_in_flight"] == 4
        assert snap["rejected_total"] == 0
        assert isinstance(snap["last_wait_ms"], float)

    def test_wait_time_is_recorded(self):
        """last_wait_ms가 0에 머물면 큐가 밀리는지 밖에서 알 수 없다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_in_flight=0)
            release = asyncio.Event()
            started = asyncio.Event()

            async def holder():
                async with q.acquire():
                    started.set()
                    await release.wait()

            h = asyncio.create_task(holder())
            await started.wait()

            async def waiter():
                async with q.acquire():
                    pass

            w = asyncio.create_task(waiter())
            await asyncio.sleep(0.02)
            release.set()
            await asyncio.gather(h, w)
            return q.last_wait_ms

        assert asyncio.run(scenario()) > 0
