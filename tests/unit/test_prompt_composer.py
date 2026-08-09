"""에이전트 프롬프트 조합·분해 — 폼의 칸과 저장 본문 사이 계약.

21종이 모두 같은 골격(# 제목 / ## 임무 / ## 분석 방법론 / ## 핵심 산출물)이라
칸으로 받아 조합한다. 조합 결과가 작성 경로의 단일 진실이고, 칸 값은 재편집용이다.
"""

from __future__ import annotations

from src.prompts import list_analysts
from src.services.prompts.composer import (
    SECTION_FIELDS,
    compose_agent_prompt,
    parse_agent_prompt,
)


class TestCompose:
    def test_composes_in_fixed_order_with_title(self):
        text = compose_agent_prompt(
            "수요분석", {"mission": "임무 본문", "method": "방법 본문", "deliverables": "산출 본문"}
        )
        assert text.startswith("# 수요분석 전문 에이전트")
        assert text.index("## 임무") < text.index("## 분석 방법론") < text.index("## 핵심 산출물")

    def test_blank_field_is_dropped(self):
        text = compose_agent_prompt("X", {"mission": "임무만", "method": "  "})
        assert "## 분석 방법론" not in text

    def test_no_volume_text_is_generated(self):
        # 분량은 volume_target에서 작성 시점에 붙인다 — 두 곳에 적히면 어긋난다.
        text = compose_agent_prompt("X", {"mission": "임무"})
        assert "분량" not in text


class TestParse:
    def test_every_catalog_agent_splits_into_three_fields(self):
        keys = [k for k, _ in SECTION_FIELDS]
        for a in list_analysts():
            parsed = parse_agent_prompt(a.prompt)
            missing = [k for k in keys if not parsed.get(k)]
            assert not missing, f"{a.name}: {missing}"

    def test_unknown_section_is_kept_in_extra(self):
        parsed = parse_agent_prompt("# 제목\n\n## 임무\n임무\n\n## 별난절\n중요한 지시")
        assert "중요한 지시" in parsed["extra"]

    def test_round_trip_preserves_bodies(self):
        original = [a for a in list_analysts() if a.name == "수요분석"][0]
        parsed = parse_agent_prompt(original.prompt)
        again = parse_agent_prompt(compose_agent_prompt("수요분석", parsed))
        assert again["mission"] == parsed["mission"]
        assert again["deliverables"] == parsed["deliverables"]
