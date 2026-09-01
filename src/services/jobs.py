"""프로젝트 부분 작업의 공용 실행대 — 요청 밖에서 돌리고 상태를 물어볼 수 있게.

**왜 생겼나**: 부분 실행이 이미 네 벌 따로 구현돼 있었다 — 업로드 색인(_INDEX_TASKS),
PM 재검증(_VERIFYING), 시사점 요약(_SUMMARIZING), 절 재작성. 각자 자기 태스크 집합과
중복 방지 플래그, 자기 폴링 엔드포인트를 갖고 있었다. 새 부분 작업을 붙일 때마다 같은
것을 또 짜는 자리라 여기로 모은다(2026-08-26).

**작게 유지한다**: 단일 워커 전제의 인프로세스 레지스트리다(events.py의 pub/sub과 같은
전제). DB 테이블로 올리는 것은 여러 워커나 재시작 후 이어받기가 필요해질 때다 — 지금
필요한 것은 "지금 도는 중인가"와 "끝났으면 결과가 뭔가"뿐이다.

**진행률을 담는 이유**: 절 재작성처럼 대상이 여러 개인 작업은 '도는 중' 한 비트로는
화면이 아무것도 못 보여준다. done/total과 마지막 라벨을 함께 들고 있는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class JobState:
    """도는 중이거나 방금 끝난 작업 하나. 같은 (project, kind)는 하나만 돈다."""

    kind: str
    total: int = 0
    done: int = 0
    # 지금(또는 마지막으로) 처리한 대상의 사람이 읽는 라벨 — 화면의 진행 문구.
    current: str = ""
    running: bool = True
    # 대상별 실패 사유 {라벨: 사유} — 부분 실패를 삼키지 않는다.
    failures: dict[str, str] = field(default_factory=dict)
    # 사람이 멈추라고 한 순간 True. 태스크를 죽이지 않고 **다음 대상으로 넘어가기 전에**
    # 본문이 스스로 확인하고 빠져나온다 — 도중에 끊으면 절이 반쪽 상태로 저장된다.
    cancelled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "running": self.running,
            "total": self.total,
            "done": self.done,
            "current": self.current,
            "failures": self.failures,
            "cancelled": self.cancelled,
        }


_JOBS: dict[tuple[UUID, str], JobState] = {}
_TASKS: set[asyncio.Task] = set()


def get_job(project_id: UUID, kind: str) -> JobState | None:
    return _JOBS.get((project_id, kind))


def is_running(project_id: UUID, kind: str) -> bool:
    job = _JOBS.get((project_id, kind))
    return bool(job and job.running)


def start_job(
    project_id: UUID,
    kind: str,
    body: Callable[[JobState], Awaitable[None]],
    *,
    total: int = 0,
) -> JobState | None:
    """작업을 백그라운드로 띄운다 — 이미 같은 종류가 돌고 있으면 None.

    body는 JobState를 받아 진행을 직접 갱신한다(done += 1 등). 예외는 여기서 잡아
    running=False로 마감한다 — 화면이 영원히 '도는 중'에 갇히지 않게.
    """
    key = (project_id, kind)
    existing = _JOBS.get(key)
    if existing and existing.running:
        return None
    job = JobState(kind=kind, total=total)
    _JOBS[key] = job

    async def _run() -> None:
        try:
            await body(job)
        except Exception:
            logger.warning("job.failed", project_id=str(project_id), kind=kind, exc_info=True)
        finally:
            job.running = False

    task = asyncio.create_task(_run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return job


def cancel_job(project_id: UUID, kind: str) -> bool:
    """멈춤 요청 — 태스크를 죽이지 않고 깃발만 세운다. 돌고 있지 않으면 False.

    asyncio.Task.cancel()로 끊지 않는 이유: 절 재작성처럼 대상마다 DB 쓰기가 있는
    작업은 아무 데서나 끊으면 절이 반쪽으로 저장된다. 본문이 대상 경계에서 확인하고
    스스로 빠져나오게 한다 — 지금 돌던 대상 하나는 끝까지 마친다.
    """
    job = _JOBS.get((project_id, kind))
    if job is None or not job.running:
        return False
    job.cancelled = True
    logger.info("job.cancel_requested", project_id=str(project_id), kind=kind)
    return True


def clear_job(project_id: UUID, kind: str) -> None:
    """끝난 작업 기록을 지운다 — 테스트와 프로젝트 삭제 정리용."""
    _JOBS.pop((project_id, kind), None)
