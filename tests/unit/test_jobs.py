"""부분 작업 공용 실행대 — 중복 방지·진행 갱신·실패 마감 (DB 없음).

계약:
- 같은 (project, kind)는 하나만 돈다. 두 번째 요청은 None을 받는다.
- 끝나면 반드시 running=False가 된다 — 예외로 죽어도 화면이 도는 중에 갇히면 안 된다.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from src.services.jobs import clear_job, get_job, is_running, start_job


class TestStartJob:
    async def test_runs_body_and_marks_finished(self):
        pid = uuid4()
        seen: list[int] = []

        async def body(job):
            job.current = "1.1 배경"
            job.done += 1
            seen.append(1)

        job = start_job(pid, "demo", body, total=1)
        assert job is not None
        await asyncio.sleep(0.05)

        assert seen == [1]
        assert job.done == 1
        assert job.current == "1.1 배경"
        assert job.running is False
        clear_job(pid, "demo")

    async def test_second_start_is_refused_while_running(self):
        pid = uuid4()
        gate = asyncio.Event()

        async def body(_job):
            await gate.wait()

        first = start_job(pid, "demo", body)
        assert first is not None
        assert is_running(pid, "demo") is True

        assert start_job(pid, "demo", body) is None, "같은 종류가 도는 중엔 새로 띄우지 않는다"

        gate.set()
        await asyncio.sleep(0.05)
        assert is_running(pid, "demo") is False
        clear_job(pid, "demo")

    async def test_restart_allowed_after_finish(self):
        pid = uuid4()

        async def body(job):
            job.done += 1

        start_job(pid, "demo", body)
        await asyncio.sleep(0.05)
        second = start_job(pid, "demo", body)
        assert second is not None, "끝난 뒤에는 다시 걸 수 있어야 한다"
        await asyncio.sleep(0.05)
        clear_job(pid, "demo")

    async def test_exception_still_closes_the_job(self):
        """예외로 죽어도 running=False — 화면이 영원히 도는 중에 갇히지 않게."""
        pid = uuid4()

        async def body(_job):
            raise RuntimeError("터짐")

        job = start_job(pid, "demo", body)
        await asyncio.sleep(0.05)

        assert job is not None
        assert job.running is False
        clear_job(pid, "demo")

    async def test_isolated_per_project_and_kind(self):
        a, b = uuid4(), uuid4()
        gate = asyncio.Event()

        async def body(_job):
            await gate.wait()

        assert start_job(a, "demo", body) is not None
        assert start_job(b, "demo", body) is not None, "다른 프로젝트는 서로 막지 않는다"
        assert start_job(a, "other", body) is not None, "다른 종류는 서로 막지 않는다"

        gate.set()
        await asyncio.sleep(0.05)
        for pid, kind in ((a, "demo"), (b, "demo"), (a, "other")):
            clear_job(pid, kind)

    async def test_status_dict_shape(self):
        pid = uuid4()

        async def body(job):
            job.failures["2.3 인구"] = "재작성 결과가 완결되지 않음"
            job.done += 1

        start_job(pid, "demo", body, total=2)
        await asyncio.sleep(0.05)

        state = get_job(pid, "demo")
        assert state is not None
        assert state.as_dict() == {
            "kind": "demo",
            "running": False,
            "total": 2,
            "done": 1,
            "current": "",
            "failures": {"2.3 인구": "재작성 결과가 완결되지 않음"},
        }
        clear_job(pid, "demo")
