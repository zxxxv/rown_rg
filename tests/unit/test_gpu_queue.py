"""GPU 큐 — 동시 실행 제한과 "타임아웃 날 요청만" 거절.

여기서 지키려는 것 둘:

1. **큐가 길다는 이유만으로 거절하지 않는다.** 기다렸다 GPU로 처리하는 편이 CPU
   폴백보다 거의 항상 빠르다(리랭킹 GPU 1.5초 vs CPU 140초). 성급한 거절은 31초면
   끝날 일을 140초로 만든다.
2. **어차피 타임아웃 날 요청은 미리 돌려보낸다.** 60초 기다린 뒤 폴백하면 200초가
   되어 즉시 폴백(140초)보다 나쁘다.
"""

from __future__ import annotations

import asyncio

import pytest

from gpu_service.app.gpu_queue import GpuBusy, GpuQueue


async def _hold(q: GpuQueue, started: asyncio.Event, release: asyncio.Event) -> None:
    async with q.acquire():
        started.set()
        await release.wait()


class TestConcurrencyLimit:
    def test_only_one_runs_at_a_time(self):
        """리랭킹과 임베딩이 같은 카드를 쓴다 - 겹쳐 돌면 VRAM이 두 배로 필요해진다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=0)
            peak = running = 0

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
            q = GpuQueue(concurrency=2, max_wait_s=0)
            peak = running = 0

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


class TestWaitingIsPreferred:
    """가장 중요한 성질 — 웬만하면 줄을 세운다."""

    def test_long_queue_alone_does_not_reject(self):
        """큐가 길어도 예상 대기가 상한 안이면 모두 받아야 한다.

        이게 깨지면 31초면 끝날 요청을 140초짜리 CPU 폴백으로 보내게 된다.
        """

        async def scenario():
            # 평균 1초짜리 작업, 상한 55초 -> 50건이 줄을 서도 예상 50초라 통과해야 한다
            q = GpuQueue(concurrency=1, max_wait_s=55.0)
            q._avg_task_s = 1.0  # 학습을 기다리지 않고 고정
            started, release = asyncio.Event(), asyncio.Event()
            holder = asyncio.create_task(_hold(q, started, release))
            await started.wait()

            accepted = 0

            async def waiter():
                nonlocal accepted
                async with q.acquire():
                    accepted += 1

            waiters = [asyncio.create_task(waiter()) for _ in range(40)]
            await asyncio.sleep(0)
            in_flight_peak = q.in_flight
            release.set()
            await asyncio.gather(holder, *waiters)
            return accepted, in_flight_peak, q.rejected

        accepted, peak, rejected = asyncio.run(scenario())
        assert rejected == 0, "큐가 길다는 이유만으로 거절했다"
        assert accepted == 40
        assert peak > 8, "예전 고정 상한(8)이 아직 걸려 있다"

    def test_rejects_only_when_wait_exceeds_limit(self):
        """예상 대기가 상한을 넘으면 그때는 거절 - 기다려도 타임아웃이라 더 느리다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=10.0)
            q._avg_task_s = 4.0  # 3건만 밀려도 12초 > 10초
            started, release = asyncio.Event(), asyncio.Event()
            holder = asyncio.create_task(_hold(q, started, release))
            await started.wait()

            async def waiter():
                async with q.acquire():
                    pass

            ws = [asyncio.create_task(waiter()) for _ in range(2)]
            await asyncio.sleep(0)
            # 이제 3건(실행1 + 대기2) -> 예상 12초 -> 다음은 거절
            with pytest.raises(GpuBusy) as exc:
                async with q.acquire():
                    pass
            release.set()
            await asyncio.gather(holder, *ws)
            return exc.value

        err = asyncio.run(scenario())
        assert err.estimated_wait_s > err.limit_s
        assert "타임아웃" in str(err)

    def test_zero_limit_never_rejects(self):
        """0은 거절 없음 - 운영에서 이 기능을 끄고 싶을 때의 탈출구."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=0)
            q._avg_task_s = 9999.0
            async with q.acquire():
                assert q.in_flight == 1
            return q.rejected

        assert asyncio.run(scenario()) == 0


class TestDurationLearning:
    def test_average_follows_actual_duration(self):
        """리랭킹(1.5초)과 임베딩(4.5초)은 세 배 차이라 고정값으로는 예측이 안 된다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=0)
            for _ in range(30):
                async with q.acquire():
                    await asyncio.sleep(0.005)
            return q.snapshot()["avg_task_s"]

        avg = asyncio.run(scenario())
        # 초기값 4.5초에서 실제 0.005초 쪽으로 내려와야 한다
        assert avg < 1.0, f"평균이 실제 처리 시간을 따라가지 않는다: {avg}"

    def test_estimate_scales_with_queue_depth(self):
        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=0)
            q._avg_task_s = 2.0
            base = q.estimated_wait_s()
            started, release = asyncio.Event(), asyncio.Event()
            h = asyncio.create_task(_hold(q, started, release))
            await started.wait()
            one = q.estimated_wait_s()
            release.set()
            await h
            return base, one

        base, one = asyncio.run(scenario())
        assert base == 0
        assert one == pytest.approx(2.0)


class TestCounters:
    def test_slot_released_after_rejection(self):
        """거절이 카운터를 오염시키면 그 뒤로 영원히 거절하게 된다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=1.0)
            q._avg_task_s = 5.0
            started, release = asyncio.Event(), asyncio.Event()
            h = asyncio.create_task(_hold(q, started, release))
            await started.wait()
            for _ in range(3):
                with pytest.raises(GpuBusy):
                    async with q.acquire():
                        pass
            release.set()
            await h
            return q.in_flight, q.rejected

        in_flight, rejected = asyncio.run(scenario())
        assert in_flight == 0, "카운터가 새고 있다"
        assert rejected == 3

    def test_counter_returns_to_zero_on_exception(self):
        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=0)
            with pytest.raises(ValueError):
                async with q.acquire():
                    raise ValueError("작업 실패")
            return q.in_flight

        assert asyncio.run(scenario()) == 0


class TestSnapshot:
    def test_exposes_state_for_health(self):
        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=55.0)
            async with q.acquire():
                pass
            return q.snapshot()

        snap = asyncio.run(scenario())
        assert snap["in_flight"] == 0
        assert snap["max_wait_s"] == 55.0
        assert snap["rejected_total"] == 0
        assert snap["completed_total"] == 1
        assert isinstance(snap["estimated_wait_s"], float)

    def test_wait_time_is_recorded(self):
        """last_wait_ms가 0에 머물면 큐가 밀리는지 밖에서 알 수 없다."""

        async def scenario():
            q = GpuQueue(concurrency=1, max_wait_s=0)
            started, release = asyncio.Event(), asyncio.Event()
            h = asyncio.create_task(_hold(q, started, release))
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


class TestInitialTaskSeed:
    """파싱 레인은 건당 수십 초다 — 4.5초 시드로 시작하면 첫 몇 건의 예상 대기가
    크게 과소평가돼 어차피 타임아웃 날 요청을 통과시킨다."""

    def test_initial_task_s_seeds_estimate(self):
        q = GpuQueue(concurrency=1, max_wait_s=300.0, initial_task_s=60.0)
        assert q.snapshot()["avg_task_s"] == 60.0

    def test_default_seed_unchanged(self):
        q = GpuQueue(concurrency=1, max_wait_s=55.0)
        assert q.snapshot()["avg_task_s"] == 4.5

    def test_two_queues_have_independent_ewma(self):
        """리랭킹 큐와 파싱 레인의 통계가 섞이면 리랭킹이 허위 429를 맞는다."""

        async def scenario():
            fast = GpuQueue(concurrency=1, max_wait_s=55.0)
            slow = GpuQueue(concurrency=1, max_wait_s=300.0, initial_task_s=60.0)
            async with fast.acquire():
                pass
            return fast.snapshot()["avg_task_s"], slow.snapshot()["avg_task_s"]

        fast_avg, slow_avg = asyncio.run(scenario())
        assert fast_avg < 60.0
        assert slow_avg == 60.0
