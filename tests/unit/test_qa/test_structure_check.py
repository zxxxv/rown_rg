"""구조 완결성 — 서두 선언 vs 본문 이행 대조(2026-08-29 철강 정독 실측 3건 고정)."""

from __future__ import annotations

from uuid import uuid4

from src.core.types import SectionPlan
from src.services.qa.structure_check import (
    count_mismatches,
    declared_enumerations,
    ganada_breaks,
    structure_findings,
    unfulfilled_items,
)


def _plan(chapter: int = 2, section: int = 1, title: str = "STEEP 분석") -> SectionPlan:
    return SectionPlan(
        section_id=uuid4(),
        chapter_number=chapter,
        section_number=section,
        chapter_title="거시환경 분석",
        title=title,
        direction="",
    )


# 철강 2.1 실측 축약본 — 5개 요인 선언, 본문은 기술·환경뿐.
_STEEP_BROKEN = (
    "본 절은 저탄소·고부가 철강산업을 둘러싼 거시환경을 사회·기술·경제·환경·정치 5개"
    " 요인별 핵심 트렌드로 정리하고 사업 기획에의 시사점을 도출함\n\n"
    "### 기술(Technological): 수소환원제철 전환 경쟁\n"
    "ㅇ 수소환원제철은 상용화 진입 단계에 있으며 각국 실증이 가속되고 있음\n\n"
    "□ 환경 부문에서는 탄소규제가 수출 조건으로 전환되고 있음\n"
    "ㅇ CBAM 확정기간 진입으로 배출 데이터 요구가 강화됨\n"
)

_STEEP_OK = (
    _STEEP_BROKEN
    + "\n□ 사회 부문은 인력 전환 수요가 커지고 있음\n"
    + "□ 경제 부문은 원가 구조 재편이 진행 중임\n"
    + "□ 정치 부문은 통상 협상이 변수임\n"
)


class TestEnumDeclaration:
    def test_서두의_열거_선언을_읽는다(self) -> None:
        decls = declared_enumerations(_STEEP_BROKEN)
        assert len(decls) == 1
        assert decls[0].items == ("사회", "기술", "경제", "환경", "정치")
        assert decls[0].kind == "요인"
        assert decls[0].count == 5

    def test_본문_중간의_열거는_계약이_아니다(self) -> None:
        body = ("배경 서술임\n" * 120) + "사회·기술·경제·환경·정치 5개 요인이 얽혀 있음"
        assert declared_enumerations(body) == []

    def test_두_개짜리_열거는_계약이_아니다(self) -> None:
        assert declared_enumerations("규제·시장 양 측면에서 살펴봄") == []


class TestUnfulfilled:
    def test_선언한_축이_본문에_없으면_잡는다(self) -> None:
        (found,) = unfulfilled_items(_STEEP_BROKEN)
        assert found[1] == ["사회", "경제", "정치"]

    def test_전부_이행하면_잡지_않는다(self) -> None:
        assert unfulfilled_items(_STEEP_OK) == []

    def test_산문_속_스침은_이행이_아니다(self) -> None:
        # 항목이 줄머리 블록이 아니라 문장 중간에만 나오면 여전히 미이행이다.
        body = _STEEP_BROKEN + "\nㅇ 다만 각국의 사회 및 경제 및 정치 여건 서술이 이어짐\n"
        (found,) = unfulfilled_items(body)
        assert found[1] == ["사회", "경제", "정치"]

    def test_라틴_열거도_같은_규칙이다(self) -> None:
        # 철강 6.1 실측: SO·ST·WO·WT 선언 후 WT 블록 부재.
        swot = (
            "이 절은 SO·ST·WO·WT 교차 전략을 도출해 확정함\n\n"
            "□ SO 전략: 강점-기회 결합\n□ ST 전략: 강점-위협 대응\n□ WO 전략: 약점 보완\n"
        )
        (found,) = unfulfilled_items(swot)
        assert found[1] == ["WT"]

    def test_결함_행은_빠진_수로_심각도를_가른다(self) -> None:
        rows = structure_findings([(_plan(), _STEEP_BROKEN)])
        missing = [r for r in rows if "블록이 없음" in r["detail"]]
        assert len(missing) == 1
        assert missing[0]["severity"] == "critical"
        assert missing[0]["section_ref"] == "2.1"
        assert "사회, 경제, 정치" in missing[0]["detail"]


class TestCountMismatch:
    def test_개수가_어긋난_선언을_잡는다(self) -> None:
        body = "동 절은 규제·보조금·조달 4개 축으로 주요국 정책을 비교함"
        (found,) = count_mismatches(body)
        assert (found.count, len(found.items)) == (4, 3)

    def test_개수가_맞으면_잡지_않는다(self) -> None:
        assert count_mismatches(_STEEP_BROKEN) == []


class TestGanada:
    def test_가만_있으면_중단으로_잡는다(self) -> None:
        # 철강 6.2 실측: "### 가."만 있고 나·다가 없었다.
        body = "### 가. 고부가 전환 전략\nㅇ 본문\n\n□ 저탄소 로드맵 서술이 이어짐\n"
        assert ganada_breaks(body) == ["소제목 체계 중단: '가.'만 있고 다음 소제목이 없음"]

    def test_연속된_벌은_정상이다(self) -> None:
        body = "### 가. 전략\nㅇ 본문\n### 나. 로드맵\nㅇ 본문\n### 다. 과제\nㅇ 본문\n"
        assert ganada_breaks(body) == []

    def test_결번은_빠진_글자를_짚는다(self) -> None:
        body = "### 가. 전략\nㅇ 본문\n### 다. 과제\nㅇ 본문\n"
        (why,) = ganada_breaks(body)
        assert "'나.'" in why

    def test_소제목이_없으면_침묵한다(self) -> None:
        assert ganada_breaks("ㅇ 일반 본문\n- 세부 항목") == []
