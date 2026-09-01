"""검증 422의 위치 표기 - 기계 좌표를 사람 좌표로(2026-08-28 실사용 보고).

프리셋 저장이 여섯 번 튕기는 동안 문구가 "chapters.2.sections.0.agents: 최대
5개…"라 사용자가 어디를 고칠지 몰랐다. 목차 꼴 좌표는 "3.1절 담당 에이전트"로
옮긴다 - 사유 번역(_friendly_reason)은 이미 있었고 좌표가 구멍이었다.
"""

from __future__ import annotations

from src.api.middleware.error_handler import _field_path


class TestFieldPath:
    def test_outline_section_field(self) -> None:
        assert (
            _field_path(("body", "chapters", 2, "sections", 0, "agents")) == "3.1절 담당 에이전트"
        )

    def test_outline_list_item_index(self) -> None:
        assert (
            _field_path(("body", "chapters", 0, "sections", 4, "key_points", 30))
            == "1.5절 핵심 포인트 31번째"
        )

    def test_chapter_level_field(self) -> None:
        assert _field_path(("body", "chapters", 1, "title")) == "2장 제목"
        assert _field_path(("body", "chapters", 3, "sections")) == "4장 절"

    def test_plain_field_translated(self) -> None:
        assert _field_path(("body", "name")) == "이름"
        assert _field_path(("body", "spec", "min_chars")) == "spec.min_chars"

    def test_unknown_shape_falls_back(self) -> None:
        assert _field_path(("body", "chapters")) == "목차"
        assert _field_path(()) == ""
