"""In-process per-IP 로그인 실패 rate limiter.

IP 화이트리스트를 0.0.0.0/0(전체 허용)로 두면 로그인이 유일한 관문이 된다. 계정 잠금은
계정당이라(5회→30분) 여러 계정에 소수 시도를 뿌리는 패스워드 스프레이나 계정잠금 DoS를
막지 못한다. 한 소스 IP의 **실패한** 로그인 시도를 창 단위로 세어, 임계 초과 시 그 IP의
로그인을 잠시 차단한다.

성공은 세지 않는다 — 그래야 사무실 여러 명이 한 공인 IP(NAT)를 공유해도 정상 로그인이
서로를 막지 않고, 연속 실패(스프레이·무차별)만 걸린다.

⚠️ 단일 워커 전제(app_settings 캐시·lockout_handler와 동일). 멀티 워커면 워커별로 카운트가
   나뉘어 실효 한도가 그만큼 느슨해지므로, 스케일아웃 시 공유 스토어(Redis 등)로 옮겨야 한다.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from src.core.clock import now

# 한 IP가 이 창 안에서 허용되는 최대 로그인 실패 수. 초과하면 창이 지날 때까지 차단.
MAX_FAILURES = 10
WINDOW = timedelta(minutes=5)
# 추적 IP 수 상한 — 넘으면 만료/빈 항목을 청소한다(스캐너로 인한 무한 증가 방지).
_MAX_TRACKED_IPS = 10_000

_failures: dict[str, deque[datetime]] = {}


def is_blocked(ip: str) -> bool:
    """이 IP가 현재 창에서 실패 한도를 넘겼으면 True. ip가 없으면 항상 False."""
    if not ip:
        return False
    dq = _failures.get(ip)
    if dq is None:
        return False
    cutoff = now() - WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    if not dq:
        del _failures[ip]
        return False
    return len(dq) >= MAX_FAILURES


def record_failure(ip: str) -> None:
    """이 IP의 로그인 실패를 1건 기록한다. ip가 없으면 무시."""
    if not ip:
        return
    current = now()
    cutoff = current - WINDOW

    if len(_failures) > _MAX_TRACKED_IPS:
        _sweep(cutoff)

    dq = _failures.get(ip)
    if dq is None:
        dq = deque()
        _failures[ip] = dq
    while dq and dq[0] < cutoff:
        dq.popleft()
    dq.append(current)


def _sweep(cutoff: datetime) -> None:
    """만료됐거나 비어버린 IP 항목을 제거해 메모리 증가를 막는다."""
    stale = [ip for ip, dq in _failures.items() if not dq or dq[-1] < cutoff]
    for ip in stale:
        del _failures[ip]


def reset(ip: str) -> None:
    """특정 IP의 실패 카운트 초기화(로그인 성공 시·테스트·운영용)."""
    _failures.pop(ip, None)


def clear() -> None:
    """전체 카운트 초기화(주로 테스트 격리용)."""
    _failures.clear()
