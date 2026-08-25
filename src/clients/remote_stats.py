"""원격 추론 호출의 누적 통계 — "조용한 폴백"을 세어서 드러낸다.

원격 클라이언트는 실패해도 폴백으로 살아나기 때문에 겉으로는 아무 일도 없다.
특히 임베딩의 ``local`` 폴백은 dtype이 다른 벡터를 색인에 넣어 **검색 품질만
조용히 떨어뜨린다** — 로그에 warning이 남지만 로그는 아무도 안 본다. 여기서
카운터로 쌓아 관리자 대시보드가 바로 보여주게 한다.

로그와 달리 리셋되는 시점이 프로세스 재시작뿐이라, "배포 이후 폴백이 몇 번
있었나"를 한 번의 조회로 답할 수 있다.
"""

from __future__ import annotations

import time
from typing import Any

from src.core.clock import now


class RemoteCallStats:
    """원격 호출 성공/폴백 카운터. 단일 프로세스 asyncio 전제라 락이 없다."""

    def __init__(self) -> None:
        self.remote_ok_total = 0
        # 전송층 순단을 즉시 1회 재시도한 횟수. 재시도로 살아난 순단은 폴백
        # 카운터에 안 잡히므로 여기서만 보인다 — 이 값이 뛰면 터널·GPU 박스
        # 네트워크가 흔들린다는 조기 신호다(2026-08-24 실사고: 순단 1건이
        # 쿨다운을 열어 원격 시도 없는 폴백 124건으로 증폭).
        self.transport_retry_total = 0
        # 폴백 횟수를 사유별로 나눈다. cooldown이 많으면 "원격이 오래 죽어 있었다",
        # error가 많으면 "간헐적으로 계속 실패한다" — 대응이 다르다.
        self.fallback_total: dict[str, int] = {"cooldown": 0, "error": 0}
        # 임베딩은 호출 1번에 텍스트 수백 개가 실린다. 폴백 1회의 피해 규모는
        # 호출 수가 아니라 항목 수라 따로 센다(리랭킹은 참고용).
        self.fallback_items_total = 0
        self.last_fallback_at: str | None = None
        self.last_error: str | None = None

    def record_ok(self) -> None:
        self.remote_ok_total += 1

    def record_transport_retry(self) -> None:
        self.transport_retry_total += 1

    def record_fallback(self, reason: str, *, items: int, error: str | None = None) -> None:
        self.fallback_total[reason] = self.fallback_total.get(reason, 0) + 1
        self.fallback_items_total += items
        self.last_fallback_at = now().isoformat()
        if error is not None:
            self.last_error = error

    def snapshot(self, *, disabled_until_monotonic: float) -> dict[str, Any]:
        cooldown_left = max(0.0, disabled_until_monotonic - time.monotonic())
        return {
            "remote_ok_total": self.remote_ok_total,
            "transport_retry_total": self.transport_retry_total,
            "fallback_total": dict(self.fallback_total),
            "fallback_items_total": self.fallback_items_total,
            "last_fallback_at": self.last_fallback_at,
            "last_error": self.last_error,
            "in_cooldown": cooldown_left > 0,
            "cooldown_remaining_s": round(cooldown_left, 1),
        }
