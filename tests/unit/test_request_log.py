"""요청 기록(request_log)의 계약.

핵심은 둘이다: (1) 기록은 요청을 절대 죽이지 않는다 — 실패는 삼켜진다,
(2) 날짜별 파일이 보존기간을 넘으면 지워진다 — 볼륨이 무한히 자라면 안 된다.
"""

from __future__ import annotations

import json
from datetime import datetime

from gpu_service.app.request_log import KST, RequestLog


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


class TestDisabled:
    def test_empty_dir_is_noop(self, tmp_path):
        log = RequestLog("")
        assert log.enabled is False
        # 어느 호출도 예외 없이 조용히 지나가야 한다 - 게이트가 꺼진 배포에서
        # 미들웨어가 매 요청 이걸 부른다.
        log.log(endpoint="rerank", code=200, ms=1.0, detail=None)
        assert log.days() == []
        assert log.read_day(_today()) is None


class TestWriteAndRead:
    def test_roundtrip(self, tmp_path):
        log = RequestLog(str(tmp_path))
        log.log(endpoint="rerank", code=200, ms=153.71, detail={"n": 24, "device": "cuda"})
        log.log(endpoint="embed", code=429, ms=3.2, detail=None)

        events = log.read_day(_today())
        assert [e["endpoint"] for e in events] == ["rerank", "embed"]
        assert events[0]["code"] == 200
        assert events[0]["ms"] == 153.7
        assert events[0]["n"] == 24
        assert events[0]["device"] == "cuda"
        assert events[1]["code"] == 429

    def test_bad_date_returns_none(self, tmp_path):
        log = RequestLog(str(tmp_path))
        # 경로 조작이 파일명 검증에서 막히는지가 이 테스트의 진짜 목적이다.
        assert log.read_day("../../etc/passwd") is None
        assert log.read_day("2026-13-99x") is None

    def test_missing_day_is_empty(self, tmp_path):
        log = RequestLog(str(tmp_path))
        assert log.read_day("2020-01-01") == []

    def test_truncated_last_line_is_skipped(self, tmp_path):
        # 전원 차단으로 마지막 줄이 잘려도 그날 전체를 잃으면 안 된다.
        path = tmp_path / f"{_today()}.jsonl"
        path.write_text(
            json.dumps({"t": 1, "endpoint": "rerank", "code": 200, "ms": 1.0})
            + "\n"
            + '{"t": 2, "endpoint": "em',
            encoding="utf-8",
        )
        log = RequestLog(str(tmp_path))
        events = log.read_day(_today())
        assert len(events) == 1


class TestDays:
    def test_newest_first_with_counts(self, tmp_path):
        (tmp_path / "2026-08-23.jsonl").write_text(
            json.dumps({"t": 1, "endpoint": "rerank", "code": 200, "ms": 1.0})
            + "\n"
            + json.dumps({"t": 2, "endpoint": "parse", "code": 504, "ms": 2.0})
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "2026-08-24.jsonl").write_text(
            json.dumps({"t": 3, "endpoint": "embed", "code": 200, "ms": 1.0}) + "\n",
            encoding="utf-8",
        )
        days = RequestLog(str(tmp_path)).days()
        assert [d["date"] for d in days] == ["2026-08-24", "2026-08-23"]
        assert days[1]["total"] == 2
        assert days[1]["errors"] == 1
        assert days[1]["by_endpoint"] == {"rerank": 1, "parse": 1}

    def test_foreign_files_ignored(self, tmp_path):
        (tmp_path / "notes.jsonl").write_text("{}", encoding="utf-8")
        assert RequestLog(str(tmp_path)).days() == []


class TestRetention:
    def test_first_write_of_day_prunes_expired(self, tmp_path):
        old = tmp_path / "2020-01-01.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        log = RequestLog(str(tmp_path), retention_days=90)
        log.log(endpoint="rerank", code=200, ms=1.0, detail=None)
        assert not old.exists()
        assert (tmp_path / f"{_today()}.jsonl").exists()

    def test_recent_days_survive(self, tmp_path):
        recent = tmp_path / f"{_today()}.jsonl"
        recent.write_text("{}\n", encoding="utf-8")
        log = RequestLog(str(tmp_path), retention_days=90)
        log.log(endpoint="rerank", code=200, ms=1.0, detail=None)
        assert recent.exists()
