"""진행 중 세부 단계 집합(events.active_steps) — 병렬 작성의 절 4개가 다 보이게.

마지막 이벤트 하나만 노출하던 _last_steps로는 병렬 4개 중 1개만 보였고, 사용자가
"나머지는 멈췄나"로 읽었다(2026-08-15). started 집합 추가/completed·failed 제거,
진행 카운터형 라벨("3/25")은 같은 계열 교체, 단계 완료 시 그 단계 잔재 정리.
"""

from __future__ import annotations

import uuid

from src.workflows.events import active_steps, emit_phase, emit_step


def _pid() -> uuid.UUID:
    return uuid.uuid4()


class TestActiveSteps:
    def test_병렬_절이_전부_보인다(self) -> None:
        pid = _pid()
        for sec in ("2.2", "2.3", "2.4", "2.5"):
            emit_step(pid, "writing", f"본문 작성 · {sec}", "started")
        assert active_steps(pid) == [f"본문 작성 · {sec}" for sec in ("2.2", "2.3", "2.4", "2.5")]

    def test_완료된_절은_빠진다(self) -> None:
        pid = _pid()
        emit_step(pid, "writing", "본문 작성 · 2.2", "started")
        emit_step(pid, "writing", "본문 작성 · 2.3", "started")
        emit_step(pid, "writing", "본문 작성 · 2.2", "completed")
        assert active_steps(pid) == ["본문 작성 · 2.3"]

    def test_실패한_절도_빠진다(self) -> None:
        pid = _pid()
        emit_step(pid, "writing", "본문 작성 · 2.2", "started")
        emit_step(pid, "writing", "본문 작성 · 2.2", "failed")
        assert active_steps(pid) == []

    def test_카운터_틱은_같은_계열을_교체한다(self) -> None:
        """색인 '청킹·임베딩 i/n'은 매 틱이 새 started고 닫히지 않는다 — 쌓이면 안 된다."""
        pid = _pid()
        emit_step(pid, "indexing", "청킹·임베딩 1/25 · 자료A", "started")
        emit_step(pid, "indexing", "청킹·임베딩 2/25 · 자료B", "started")
        emit_step(pid, "indexing", "청킹·임베딩 3/25 · 자료C", "started")
        assert active_steps(pid) == ["청킹·임베딩 3/25 · 자료C"]

    def test_단계_완료가_그_단계_잔재를_치운다(self) -> None:
        pid = _pid()
        emit_step(pid, "indexing", "청킹·임베딩 25/25 · 마지막", "started")
        emit_step(pid, "writing", "본문 작성 · 1.1", "started")
        emit_phase(pid, "indexing", "completed")
        assert active_steps(pid) == ["본문 작성 · 1.1"]

    def test_없는_프로젝트는_빈_목록(self) -> None:
        assert active_steps(_pid()) == []
