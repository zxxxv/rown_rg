"""절 의존 그래프 초안 — AI가 뽑고, 정화가 목차에 대고 걸러낸다.

이어받기와 작성 순서는 한 질문이다: 앞 절 내용이 있어야 쓸 수 있으니 순차로 쓰는 것이다.
그래서 사람에게 두 번 묻지 않고 그래프 하나만 만든다. 손으로 적으라고 했더니 아무도 안
적었다 — 예타 프리셋 146절 중 5절, v6 런 주입 적립 0건.
"""

from __future__ import annotations

import pytest

from src.core.types import SectionPlan
from src.services.generation.dependency_graph import _parse, draft_builds_on, sanitize


def _plan() -> list[SectionPlan]:
    return [
        SectionPlan(chapter_number=c, section_number=s, title=f"{c}.{s} 절", chapter_title=f"{c}장")
        for c, s in [(1, 1), (1, 2), (2, 1), (2, 2), (2, 3), (3, 1)]
    ]


class TestParse:
    def test_reads_a_fenced_answer(self):
        raw = '설명입니다\n```json\n{"deps":[{"section":"2.3","builds_on":["2.1"]}]}\n```'
        assert _parse(raw) == {"2.3": ["2.1"]}

    def test_garbage_is_an_empty_graph_not_an_error(self):
        """못 읽으면 빈 그래프다 — 그래프 하나 때문에 런이 죽으면 안 된다."""
        assert _parse("죄송하지만 못 하겠습니다") == {}
        assert _parse('{"deps": "이상한 모양"}') == {}


class TestSanitize:
    def test_drops_ghosts_self_and_forward_refs(self):
        """뒤를 가리키는 참조는 버린다 — 순서와 한 몸이라 기다리면 교착이다."""
        got = sanitize(
            _plan(),
            {
                "2.3": ["2.1", "2.2"],  # 정상
                "1.1": ["2.1"],  # 뒤 참조 — 버린다
                "3.1": ["9.9", "3.1", "2.*"],  # 유령·자기 참조는 버리고 장 참조만 남는다
                "9.9": ["1.1"],  # 없는 절이 주어 — 통째로 버린다
            },
        )
        assert got == {"2.3": ["2.1", "2.2"], "3.1": ["2.*"]}

    def test_chapter_ref_must_be_fully_behind(self):
        """'2.*'는 2장이 다 끝나야 받을 수 있다 — 2장 안의 절은 자기 장을 못 받는다."""
        assert sanitize(_plan(), {"2.2": ["2.*"]}) == {}
        assert sanitize(_plan(), {"3.1": ["2.*"]}) == {"3.1": ["2.*"]}

    def test_cap_is_enforced(self):
        from src.core.builds_on import MAX_REFS_PER_SECTION

        got = sanitize(_plan(), {"3.1": ["1.1", "1.2", "2.1", "2.2"]})
        assert len(got["3.1"]) == MAX_REFS_PER_SECTION


class _FakeClient:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def complete(self, _req):
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.content = self.content
        return r


class TestDraft:
    @pytest.mark.asyncio
    async def test_drafts_and_sanitizes(self):
        client = _FakeClient('{"deps":[{"section":"2.3","builds_on":["2.1","9.9"]}]}')
        got = await draft_builds_on(_plan(), client=client, model="m")
        assert got == {"2.3": ["2.1"]}
        assert client.calls == 1

    @pytest.mark.asyncio
    async def test_model_failure_does_not_block_the_run(self):
        class _Boom:
            async def complete(self, _req):
                raise RuntimeError("모델 실패")

        assert await draft_builds_on(_plan(), client=_Boom(), model="m") == {}

    @pytest.mark.asyncio
    async def test_single_section_needs_no_call(self):
        client = _FakeClient("{}")
        plan = [SectionPlan(chapter_number=1, section_number=1, title="유일한 절")]
        assert await draft_builds_on(plan, client=client, model="m") == {}
        assert client.calls == 0, "절이 하나뿐인데 모델을 불렀다"
