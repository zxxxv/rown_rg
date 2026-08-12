"""개인 프롬프트 생성 계약 — 본문 아니면 칸(sections).

칸만 채운 저장이 content min_length=1에 걸려 422로 잘리던 결함(2026-08-12 QA).
서비스는 칸으로 본문을 조합하도록 설계돼 있으므로, 스키마도 같은 계약이어야 한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas.prompt import PersonalPromptCreate


class TestBodyOrSections:
    def test_agent_sections_only_is_valid(self):
        """칸(임무 등)만 채우고 content가 비어도 저장 가능해야 한다 — 서버가 조합한다."""
        data = PersonalPromptCreate(
            kind="agent",
            name="테스트 에이전트",
            content="",
            spec={"sections": {"mission": "시장 동향을 분석한다"}},
        )
        assert data.spec.sections["mission"]

    def test_agent_content_only_is_valid(self):
        data = PersonalPromptCreate(kind="agent", name="자유 편집", content="전체 원문")
        assert data.content == "전체 원문"

    def test_agent_neither_is_rejected_in_korean(self):
        with pytest.raises(ValidationError) as exc:
            PersonalPromptCreate(kind="agent", name="빈 에이전트", content="")
        assert "본문 또는 칸" in str(exc.value)

    def test_whitespace_sections_do_not_count(self):
        """공백만 있는 칸은 채운 것이 아니다 — 조합하면 제목 줄만 남는다."""
        with pytest.raises(ValidationError):
            PersonalPromptCreate(
                kind="agent",
                name="공백 칸",
                content="  ",
                spec={"sections": {"mission": "   "}},
            )

    def test_rule_requires_content(self):
        """작성 규칙에는 칸 개념이 없다 — 본문 필수."""
        with pytest.raises(ValidationError) as exc:
            PersonalPromptCreate(kind="rule", name="빈 규칙", content="")
        assert "규칙 본문" in str(exc.value)
