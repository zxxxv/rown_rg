"""미반영 판정 — 설계 변경이 본문에 닿았는지 가리는 순수 계약 (DB 없음).

계약:
- 지문은 **작성에 영향을 주는 필드**만 본다. 번호가 밀린 절은 미반영이 아니다.
- plan_hash가 빈 옛 절은 판정하지 않는다(기존 보고서가 통째로 미반영으로 뜨면 안 된다).
- 본문 없음·목차 수정·자료 제외는 각각 별개 사유이고 함께 붙을 수 있다.
"""

from __future__ import annotations

from uuid import uuid4

from src.core.types import SectionPlan
from src.services.sections.drift import (
    SectionSnapshot,
    content_fingerprint,
    detect_drift,
)


def _plan(**over) -> SectionPlan:
    base = {
        "chapter_number": 2,
        "section_number": 3,
        "title": "인구·고령화 영향",
        "chapter_title": "환경 분석",
        "direction": "수요에 미치는 영향을 본다",
        "key_points": ["고령화율", "수도권 집중"],
        "analysts": ["STEEP분석"],
    }
    base.update(over)
    return SectionPlan(**base)


def _snap(plan: SectionPlan, **over) -> SectionSnapshot:
    base = {
        "section_id": plan.section_id,
        "has_content": True,
        "plan_hash": content_fingerprint(plan),
    }
    base.update(over)
    return SectionSnapshot(**base)


class TestContentFingerprint:
    def test_stable_for_same_plan(self):
        plan = _plan()
        assert content_fingerprint(plan) == content_fingerprint(_plan())

    def test_position_change_does_not_matter(self):
        """번호만 밀린 절은 같은 본문을 그대로 쓴다 — 절 정체성은 안정 id가 지킨다."""
        assert content_fingerprint(_plan()) == content_fingerprint(
            _plan(chapter_number=4, section_number=1)
        )

    def test_direction_change_matters(self):
        assert content_fingerprint(_plan()) != content_fingerprint(_plan(direction="딴 방향"))

    def test_key_points_change_matters(self):
        assert content_fingerprint(_plan()) != content_fingerprint(_plan(key_points=["딴 것"]))

    def test_analysts_change_matters(self):
        assert content_fingerprint(_plan()) != content_fingerprint(_plan(analysts=["정책동향"]))

    def test_volume_target_change_matters(self):
        """분량 목표는 검색 결과를 안 바꾸지만 결과물을 바꾼다 — 검색 지문과 분리한 이유."""
        assert content_fingerprint(_plan()) != content_fingerprint(_plan(min_chars=4500))

    def test_builds_on_change_matters(self):
        assert content_fingerprint(_plan()) != content_fingerprint(_plan(builds_on=["4.1"]))


class TestDetectDrift:
    def test_unchanged_section_is_not_listed(self):
        plan = _plan()
        assert detect_drift([plan], {plan.section_id: _snap(plan)}) == []

    def test_plan_change_is_flagged(self):
        written = _plan()
        snapshots = {written.section_id: _snap(written)}
        edited = _plan(section_id=written.section_id, direction="완전히 다른 방향")

        drift = detect_drift([edited], snapshots)

        assert len(drift) == 1
        assert drift[0].reasons == ("plan_changed",)
        assert drift[0].label == "2.3 인구·고령화 영향"

    def test_missing_content_is_flagged(self):
        # 본문이 있는 절이 하나는 있어야 판정이 선다 - 없으면 미작성 프로젝트다.
        written = _plan(chapter_number=9, section_number=9, title="이미 쓴 절")
        plan = _plan()
        drift = detect_drift(
            [plan, written],
            {
                plan.section_id: _snap(plan, has_content=False),
                written.section_id: _snap(written),
            },
        )
        assert drift[0].reasons == ("missing",)

    def test_section_absent_from_rows_is_missing(self):
        """목차에 새로 넣은 절 — 행 자체가 없다(쓰인 절이 있는 프로젝트에서)."""
        written = _plan(chapter_number=9, section_number=9, title="이미 쓴 절")
        plan = _plan()
        drift = detect_drift([plan, written], {written.section_id: _snap(written)})
        assert drift[0].reasons == ("missing",)

    def test_never_written_project_has_no_drift(self):
        """한 번도 안 쓴 프로젝트는 미반영이 아니라 미작성이다 - 신규 프로젝트의
        자료 검토 화면에 '미반영 12'가 떴던 실물 사고(2026-08-27)."""
        a = _plan(chapter_number=1, section_number=1)
        b = _plan(chapter_number=1, section_number=2)
        assert detect_drift([a, b], {}) == []
        # 한 번도 안 쓴 행은 본문도 지문(plan_hash)도 없다 - 억제 판별자는 작성
        # 흔적이므로 둘 다 비워야 미작성으로 억제된다(2026-09-03 수리).
        never = _snap(a, has_content=False, plan_hash="")
        assert detect_drift([a], {a.section_id: never}) == []
        # 반대로 지문이 남은 빈 절(작성 후 비워짐)은 missing으로 떠야 한다 -
        # 완료 프로젝트의 빈 절이 기각 시도 후 목록에서 사라지던 결함의 회귀 가드.
        emptied = _snap(a, has_content=False)
        [drift] = detect_drift([a], {a.section_id: emptied})
        assert drift.reasons == ("missing",)

    def test_excluded_source_is_flagged_with_ids(self):
        plan = _plan()
        dropped = uuid4()
        drift = detect_drift([plan], {plan.section_id: _snap(plan, excluded_source_ids=(dropped,))})
        assert drift[0].reasons == ("source_excluded",)
        assert drift[0].excluded_source_ids == (dropped,)

    def test_reasons_combine(self):
        written = _plan()
        dropped = uuid4()
        snapshots = {
            written.section_id: _snap(written, excluded_source_ids=(dropped,)),
        }
        edited = _plan(section_id=written.section_id, key_points=["새 포인트"])

        drift = detect_drift([edited], snapshots)

        assert drift[0].reasons == ("plan_changed", "source_excluded")

    def test_legacy_rows_without_hash_are_not_flagged(self):
        """지문 기록 이전에 쓰인 절 — 통째로 미반영으로 뜨면 화면이 쓸모없어진다."""
        written = _plan()
        snapshots = {written.section_id: _snap(written, plan_hash="")}
        edited = _plan(section_id=written.section_id, direction="딴 방향")

        assert detect_drift([edited], snapshots) == []

    def test_order_follows_outline(self):
        a = _plan(chapter_number=1, section_number=1, title="가")
        b = _plan(chapter_number=2, section_number=1, title="나")
        written = _plan(chapter_number=3, section_number=1, title="이미 쓴 절")
        drift = detect_drift([a, b, written], {written.section_id: _snap(written)})
        assert [d.label for d in drift] == ["1.1 가", "2.1 나"]
