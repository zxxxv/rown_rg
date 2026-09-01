"""키포인트 커버리지 - 결정적 대조(의역 허용·보수 임계)."""

from __future__ import annotations

from src.services.qa.keypoints import missed_keypoints


class TestMissedKeypoints:
    BODY = (
        "ㅇ 국내 기업의 재생에너지 조달 비용은 녹색프리미엄 중심으로 형성됨. "
        "PPA 제도의 제약이 커서 직접 조달이 어려움."
    )

    def test_uncovered_point_detected(self):
        missed = missed_keypoints(
            self.BODY,
            ["재생에너지 조달 비용 구조", "EU 탄소국경조정제도 인증서 가격 전망"],
        )
        assert missed == ["EU 탄소국경조정제도 인증서 가격 전망"]

    def test_paraphrase_counts_as_covered(self):
        missed = missed_keypoints(self.BODY, ["PPA 제도 제약"])
        assert missed == []

    def test_empty_inputs(self):
        assert missed_keypoints("", ["a"]) == []
        assert missed_keypoints(self.BODY, []) == []


class TestKeypointFindings:
    def test_missed_points_become_warning_row(self):
        from src.core.types import SectionPlan
        from src.services.qa.keypoints import keypoint_findings

        plan = SectionPlan(chapter_number=1, section_number=1, title="개요")
        rows = keypoint_findings(
            [(plan, "ㅇ 재생에너지 조달 비용 서술", ["재생에너지 조달 비용", "제도 시행 일정"])]
        )
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert "제도 시행 일정" in str(rows[0]["detail"])
        assert "자료 보강" in str(rows[0]["detail"])

    def test_covered_sections_silent(self):
        from src.core.types import SectionPlan
        from src.services.qa.keypoints import keypoint_findings

        plan = SectionPlan(chapter_number=1, section_number=1, title="개요")
        assert (
            keypoint_findings([(plan, "ㅇ 재생에너지 조달 비용 서술", ["재생에너지 조달 비용"])])
            == []
        )
