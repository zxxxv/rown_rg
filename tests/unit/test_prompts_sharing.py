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
    _shared_name,
    _unique_name,
    personal_spec,
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


class TestSharedName:
    """공유 이름은 **항상** 소유자가 붙는다 — 안 그러면 민짜 이름의 주인이 목록 순서로
    정해져, 남이 자기 것을 한 번 저장하는 것만으로 내 목차의 배정이 옮겨간다."""

    def test_always_appends_owner(self):
        assert _shared_name("시장분석", "홍길동", uuid.uuid4(), set()) == "시장분석 (홍길동)"

    def test_same_name_two_owners_never_collide(self):
        taken: set[str] = set()
        first = _shared_name("STEEP분석 (내 버전)", "최재웅", uuid.uuid4(), taken)
        taken.add(first)
        second = _shared_name("STEEP분석 (내 버전)", "신지영", uuid.uuid4(), taken)

        assert first != second
        assert first.endswith("(최재웅)")
        assert second.endswith("(신지영)")

    def test_order_does_not_change_who_gets_which_name(self):
        """A·B 순서를 뒤집어도 각자의 이름이 그대로여야 한다(순서 의존 제거)."""
        pa, pb = uuid.uuid4(), uuid.uuid4()

        def build(order):
            taken: set[str] = set()
            out = {}
            for owner, pid in order:
                out[owner] = _shared_name("같은이름", owner, pid, taken)
                taken.add(out[owner])
            return out

        assert build([("A", pa), ("B", pb)]) == build([("B", pb), ("A", pa)])

    def test_same_owner_name_falls_back_to_id_tail(self):
        pid = uuid.uuid4()
        got = _shared_name("시장분석", "홍길동", pid, {"시장분석 (홍길동)"})
        assert got == f"시장분석 (홍길동·{str(pid)[:4]})"


class TestSharedSpec:
    def test_marks_owner_and_shared(self):
        spec = shared_spec(_row("탄소규제동향분석"), "최재웅", set())
        assert spec.shared is True
        assert spec.owner_name == "최재웅"
        assert spec.id.startswith("u-")
        assert spec.name == "탄소규제동향분석 (최재웅)"  # 공유는 언제나 소유자가 붙는다

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


class TestPersonalSpecIsAlwaysACopy:
    """개인 에이전트는 사본이다 — 시스템 원본을 덮어쓰지 않는다(2026-08-25 사용자 결정).

    덮어쓰기 시절엔 두 가지가 조용히 먹혔다: 원본이 목록에서 사라졌고, 표시 이름이
    시스템 것으로 고정돼 "STEEP분석 (내 버전)"으로 저장해도 '내 에이전트' 칸에 안 떴다.
    """

    def _base(self) -> AnalystSpec:
        return next(a for a in list_analysts() if a.queries and a.volume_target)

    def test_copy_keeps_own_name_and_prompt(self):
        base = self._base()
        row = _row(f"{base.name} (내 버전)", content="내가 고친 프롬프트")
        row.base_ref = base.id
        spec = personal_spec(row, base, {a.name for a in list_analysts()})

        assert spec.id == f"u-{row.id}"  # '내 에이전트' 칸에 뜨는 id 규약
        assert spec.name == f"{base.name} (내 버전)"
        assert spec.prompt == "내가 고친 프롬프트"

    def test_copy_inherits_volume_and_queries_from_base(self):
        # 폼에 입력 칸이 없어 비워지는 값들 — 안 물려받으면 사본이 원본의 열화판이 된다
        base = self._base()
        row = _row("사본")
        row.base_ref = base.id
        spec = personal_spec(row, base, set())

        assert spec.volume_target == base.volume_target
        assert spec.queries == base.queries
        assert spec.cat == base.cat

    def test_own_values_win_over_base(self):
        base = self._base()
        row = _row("사본", spec={"min_chars": 1000, "max_chars": 2000, "queries": ["내 질의"]})
        row.base_ref = base.id
        row.cat = "내분류"
        spec = personal_spec(row, base, set())

        assert spec.queries == ["내 질의"]
        assert spec.cat == "내분류"
        assert spec.volume_target is not None
        assert spec.volume_target.min_chars == 1000

    def test_name_clash_with_system_is_disambiguated(self):
        # 목차는 에이전트를 이름으로 참조한다 — 같은 이름 둘이면 배정이 갈린다
        base = self._base()
        row = _row(base.name)
        row.base_ref = base.id
        spec = personal_spec(row, base, {a.name for a in list_analysts()})

        assert spec.name != base.name
        assert base.name in spec.name

    def test_blank_agent_without_base(self):
        spec = personal_spec(_row("맨바닥"), None, set())

        assert spec.cat == "개인"
        assert spec.queries == []
        assert spec.volume_target is not None  # 분량이 비면 절이 짧아진다
