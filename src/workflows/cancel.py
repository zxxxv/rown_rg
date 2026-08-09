"""실행 취소 신호 — 인프로세스 레지스트리(단일 워커 전제).

파이프라인·작성 루프가 단계·절 경계에서 ``raise_if_cancelled``로 확인하고, ``RunCancelled``가
올라오면 러너가 잡아 status=CANCELLED로 마무리한다. 취소 엔드포인트가 ``request()``로 신호하고,
러너가 관측 후 ``clear()``로 정리한다.

⚠️ events.py와 같은 단일 워커 전제 — 러너 태스크와 취소 요청이 같은 프로세스라 인메모리 set으로
   신호가 성립한다. 멀티워커면 외부 신호(DB 플래그 등)가 필요하다.
"""

from __future__ import annotations

import uuid


class RunCancelled(Exception):
    """사용자가 실행 취소를 요청함 — 협조적 중단 신호(러너가 CANCELLED로 마무리)."""


_REQUESTED: set[uuid.UUID] = set()


def request(project_id: uuid.UUID) -> None:
    _REQUESTED.add(project_id)


def clear(project_id: uuid.UUID) -> None:
    _REQUESTED.discard(project_id)


def is_requested(project_id: uuid.UUID) -> bool:
    return project_id in _REQUESTED


def raise_if_cancelled(project_id: uuid.UUID) -> None:
    """취소가 요청됐으면 RunCancelled를 올린다(단계·절 경계에서 호출)."""
    if project_id in _REQUESTED:
        raise RunCancelled(str(project_id))
