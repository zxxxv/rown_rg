"""요청 단위 영구 기록 — "그날 무슨 일이 있었나"를 재시작 후에도 답하게 한다.

StatsHistory(1시간 링버퍼)와 역할이 다르다: 그쪽은 "지금 흐름"의 그래프 원천이고
재시작에 날아가는 것을 감수한다. 여기는 **요청 하나가 한 줄**인 JSONL을 날짜별
파일로 남긴다 — "어제 색인이 몇 시에 돌았고 몇 건이 429였나"는 링버퍼로는 답할
수 없다(2026-08-24 사용자 요구).

- 파일명 날짜는 **KST 고정(+9)**이다. 컨테이너 TZ(UTC)를 따르면 자정~오전 9시
  요청이 사용자 기준 "어제" 파일에 들어간다. KST는 DST가 없어 고정 오프셋이 정확하다.
- SQLite가 아니라 JSONL인 이유: 쓰기는 append 한 줄이 전부고, 읽기는 날짜 단위
  통째로다. 동시 쓰기도 프로세스 하나뿐이라 저널링이 살 게 없다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("gpu_service")

KST = timezone(timedelta(hours=9), "KST")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 하루치 응답 상한. 전량 재색인이 몰린 날도 임베딩 청크 요청이 수천 건 수준이라
# 정상적으로는 닿지 않는다 - 폭주(재시도 루프 등)로부터 응답 크기를 지키는 안전판.
_MAX_EVENTS_PER_DAY = 50_000


class RequestLog:
    """날짜별 JSONL 요청 기록. dir가 비면 전부 no-op — 게이트는 설정이 소유한다."""

    def __init__(self, log_dir: str, *, retention_days: int = 90) -> None:
        self._dir = Path(log_dir) if log_dir else None
        self._retention_days = retention_days
        # 핸들러는 이벤트 루프에서 부른다. append 한 줄이라 블로킹은 µs 수준이지만,
        # 날짜 전환 시 보존 정리와 겹치면 순서가 꼬일 수 있어 잠금으로 직렬화한다.
        self._lock = threading.Lock()
        self._current_date: str | None = None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    def log(self, *, endpoint: str, code: int, ms: float, detail: dict[str, Any] | None) -> None:
        """한 요청을 한 줄로. 기록 실패가 요청을 죽이면 안 되므로 예외는 삼킨다."""
        if self._dir is None:
            return
        now = datetime.now(KST)
        event: dict[str, Any] = {
            "t": int(now.timestamp()),
            "endpoint": endpoint,
            "code": code,
            "ms": round(ms, 1),
        }
        if detail:
            event.update(detail)
        date = now.strftime("%Y-%m-%d")
        line = json.dumps(event, ensure_ascii=False) + "\n"
        try:
            with self._lock:
                if date != self._current_date:
                    # 날짜가 넘어가는 첫 요청이 보존 정리를 겸한다 - 별도 타이머가
                    # 없어도 파일 수가 retention_days + 1을 넘지 않는다.
                    self._prune(now)
                    self._current_date = date
                with (self._dir / f"{date}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(line)
        except OSError:
            logger.exception("request log write failed")

    def _prune(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=self._retention_days)).strftime("%Y-%m-%d")
        for path in self._dir.glob("*.jsonl"):
            if _DATE_RE.match(path.stem) and path.stem < cutoff:
                try:
                    path.unlink()
                except OSError:
                    logger.exception("request log prune failed: %s", path.name)

    def days(self) -> list[dict[str, Any]]:
        """기록이 있는 날짜와 집계 — 뷰의 날짜 목록. 최신이 앞."""
        if self._dir is None:
            return []
        out: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.jsonl"), reverse=True):
            if not _DATE_RE.match(path.stem):
                continue
            by_endpoint: dict[str, int] = {}
            errors = 0
            total = 0
            for event in self._iter_events(path):
                total += 1
                ep = event.get("endpoint", "?")
                by_endpoint[ep] = by_endpoint.get(ep, 0) + 1
                if int(event.get("code", 0)) >= 400:
                    errors += 1
            out.append(
                {"date": path.stem, "total": total, "errors": errors, "by_endpoint": by_endpoint}
            )
        return out

    def read_day(self, date: str) -> list[dict[str, Any]] | None:
        """하루치 이벤트 전부(시간순). 형식이 틀리면 None — 경로 조작도 여기서 막힌다."""
        if self._dir is None or not _DATE_RE.match(date):
            return None
        path = self._dir / f"{date}.jsonl"
        if not path.exists():
            return []
        return list(self._iter_events(path, limit=_MAX_EVENTS_PER_DAY))

    def _iter_events(self, path: Path, limit: int | None = None):
        try:
            with path.open(encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if limit is not None and i >= limit:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        # 전원 차단으로 마지막 줄이 잘릴 수 있다. 한 줄 버리고 계속.
                        continue
        except OSError:
            logger.exception("request log read failed: %s", path.name)
