"""전역 동시 실행 상한 — FIFO 대기열·중복 가드·대기 중 취소 (실DB·실LLM 없음).

runner._execute를 fake로 바꾸고 세마포어를 테스트별로 갈아끼워 검증한다.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.workflows import cancel, runner


@pytest.fixture
def slots1(monkeypatch: pytest.MonkeyPatch):
    """동시 1개 슬롯 + 깨끗한 대기열/가드 상태."""
    monkeypatch.setattr(runner, "_run_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(runner, "_WAITING", [])
    monkeypatch.setattr(runner, "_RUNNING", set())


async def _drain_tasks() -> None:
    """spawn된 백그라운드 태스크가 모두 끝날 때까지 기다린다."""
    while runner._TASKS:
        await asyncio.gather(*list(runner._TASKS), return_exceptions=True)


class TestRunQueue:
    async def test_fifo_over_capacity(self, slots1, monkeypatch: pytest.MonkeyPatch):
        """상한 1에서 3개 시작 → 실행 순서가 스폰 순서(FIFO)와 일치한다."""
        order: list[str] = []
        gate = asyncio.Event()

        async def fake_execute(pid):
            order.append(str(pid))
            await gate.wait()

        monkeypatch.setattr(runner, "_execute", fake_execute)
        p1, p2, p3 = uuid4(), uuid4(), uuid4()
        assert runner._spawn_guarded(p1) is True
        assert runner._spawn_guarded(p2) is True
        assert runner._spawn_guarded(p3) is True
        await asyncio.sleep(0)  # p1이 슬롯을 잡고 실행 진입
        assert order == [str(p1)]
        # p2·p3는 대기열 — 위치는 스폰 순서대로 1·2번
        assert runner.queue_status(p2) == {"position": 1, "waiting_total": 2}
        assert runner.queue_status(p3) == {"position": 2, "waiting_total": 2}
        assert runner.queue_status(p1) is None  # 실행 중은 대기열이 아니다
        gate.set()
        await _drain_tasks()
        assert order == [str(p1), str(p2), str(p3)]
        assert runner._WAITING == [] and runner._RUNNING == set()

    async def test_duplicate_spawn_rejected_while_queued(
        self, slots1, monkeypatch: pytest.MonkeyPatch
    ):
        """대기 중인 프로젝트를 다시 시작하려 하면 False(중복 가드가 대기열까지 포함)."""
        gate = asyncio.Event()

        async def fake_execute(pid):
            await gate.wait()

        monkeypatch.setattr(runner, "_execute", fake_execute)
        p1, p2 = uuid4(), uuid4()
        runner._spawn_guarded(p1)
        runner._spawn_guarded(p2)
        await asyncio.sleep(0)
        assert runner._spawn_guarded(p2) is False  # 대기 중 중복
        assert runner._spawn_guarded(p1) is False  # 실행 중 중복
        assert runner.is_running(p2) is True  # 대기도 '진행 중'으로 취급(삭제 차단 등)
        gate.set()
        await _drain_tasks()

    async def test_cancel_while_queued_skips_execution(
        self, slots1, monkeypatch: pytest.MonkeyPatch
    ):
        """대기 중 취소가 요청되면 슬롯을 받아도 실행하지 않고 빠진다."""
        executed: list[str] = []
        gate = asyncio.Event()

        async def fake_execute(pid):
            executed.append(str(pid))
            await gate.wait()

        monkeypatch.setattr(runner, "_execute", fake_execute)
        p1, p2 = uuid4(), uuid4()
        runner._spawn_guarded(p1)
        runner._spawn_guarded(p2)
        await asyncio.sleep(0)
        cancel.request(p2)  # p2는 아직 대기열
        gate.set()
        await _drain_tasks()
        assert executed == [str(p1)]  # p2는 실행 자체가 없었다
        assert cancel.is_requested(p2) is False  # 풀런 경로는 종료 시 플래그 정리

    async def test_collect_more_keeps_cancel_flag(self, slots1, monkeypatch: pytest.MonkeyPatch):
        """보충 수집 경로는 종료 후에도 취소 플래그를 정리하지 않는다(기존 의미 보존)."""

        async def fake_collect(pid):
            return None

        monkeypatch.setattr(runner, "_collect_more", fake_collect)
        p = uuid4()
        cancel.request(p)
        # 이미 취소 요청된 상태로 대기열에 들어가면 실행 없이 빠지되 플래그는 유지
        runner._spawn_collect_more(p)
        await _drain_tasks()
        assert cancel.is_requested(p) is True
        cancel.clear(p)
