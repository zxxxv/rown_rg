"""개인 에이전트 공개 공유 — 이름 유일화·공개 대상 제한·런 스냅샷(순수 부분).

공개 층은 DB가 필요하지만(3층 병합은 통합 테스트), 이름 충돌 처리와 스냅샷 복원은
순수 함수라 여기서 못 박는다. 목차·프리셋이 에이전트를 **이름**으로 참조하기 때문에
(OutlineEditor 칩이 a.name을 넣는다) 같은 이름 둘이 목록에 있으면 배정이 어느 쪽인지
갈린다 — 그 갈림을 막는 계약이 _unique_name이다.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.exceptions import ValidationError
from src.db.models.user_prompt import UserPrompt
from src.prompts import AnalystSpec, list_analysts
from src.services.prompts.personal import (
    _check_public,
    _unique_name,
    shared_spec,
    specs_from_snapshot,
)


def _row(name: str, content: str = "너는 분석가다.", spec: dict | None = None) -> UserPrompt:
    return UserPrompt(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        kind="agent",
        name=name,
        content=content,
        spec=spec or {},
        description="설명",
        cat=None,
    )


class TestUniqueName:
    def test_keeps_original_when_free(self):
        """안 겹치면 이름 그대로 — 이미 저장된 목차의 배정 참조가 깨지면 안 된다."""
        assert _unique_name("탄소규제분석", "홍길동", uuid.uuid4(), set()) == "탄소규제분석"

    def test_appends_owner_on_collision(self):
        got = _unique_name("시장분석", "홍길동", uuid.uuid4(), {"시장분석"})
        assert got == "시장분석 (홍길동)"

    def test_appends_id_tail_when_same_owner_name_collides(self):
        """동명이인이 같은 이름으로 공개해도 목록 안에서는 갈라야 한다."""
        pid = uuid.uuid4()
        got = _unique_name("시장분석", "홍길동", pid, {"시장분석", "시장분석 (홍길동)"})
        assert got == f"시장분석 (홍길동·{str(pid)[:4]})"


class TestSharedSpec:
    def test_marks_owner_and_shared(self):
        spec = shared_spec(_row("탄소규제동향분석"), "최재웅", set())
        assert spec.shared is True
        assert spec.owner_name == "최재웅"
        assert spec.id.startswith("u-")

    def test_volume_falls_back_to_normal(self):
        """분량 목표가 없으면 절 분량 목표가 통째로 사라진다 — 개인 층과 같은 기본값."""
        spec = shared_spec(_row("무분량"), "최재웅", set())
        assert spec.volume_target is not None
        assert spec.volume_target.min_chars > 0

    def test_respects_declared_volume(self):
        spec = shared_spec(
            _row("긴분석", spec={"min_chars": 20000, "max_chars": 30000}), "최재웅", set()
        )
        assert spec.volume_target is not None
        assert spec.volume_target.min_chars == 20000


class TestCheckPublic:
    def test_rule_cannot_be_public(self):
        """작성 규칙은 프로젝트가 소유자 스코프로 id를 검증해 붙인다 — 공개해도
        남이 쓸 길이 없으니 켜지는데 아무 일도 안 하는 거짓 스위치가 된다."""
        with pytest.raises(ValidationError):
            _check_public("rule", True)

    def test_rule_may_stay_private(self):
        _check_public("rule", False)

    def test_agent_may_be_public(self):
        _check_public("agent", True)


class TestSpecsFromSnapshot:
    def test_snapshot_overrides_system_entry_by_id(self):
        base = list_analysts()[0]
        frozen = base.model_copy(update={"prompt": "얼린 프롬프트"}).model_dump(mode="json")
        restored = {s.id: s for s in specs_from_snapshot([frozen])}
        assert restored[base.id].prompt == "얼린 프롬프트"

    def test_snapshot_appends_db_only_entries(self):
        extra = AnalystSpec(
            id="u-00000000-0000-0000-0000-000000000001",
            name="공개된 남의 에이전트",
            cat="공유",
            desc="",
            prompt="본문",
            shared=True,
            owner_name="홍길동",
        ).model_dump(mode="json")
        names = [s.name for s in specs_from_snapshot([extra])]
        assert "공개된 남의 에이전트" in names
        assert len(names) == len(list_analysts()) + 1

    def test_broken_items_are_dropped_not_raised(self):
        """옛 런의 config를 읽다 실행이 죽으면 안 된다 — 형태가 깨진 항목은 버린다."""
        specs = specs_from_snapshot([{"nope": 1}, "문자열", None])
        assert len(specs) == len(list_analysts())
