"""절 간 중복·떠 있는 참조 검출 — 실측 사례를 그대로 시험한다."""

from __future__ import annotations

from src.services.qa.cross_section import (
    DUPLICATE_THRESHOLD,
    dangling_references,
    duplicate_pairs,
)
from src.services.qa.evidence_findings import cross_section_findings

# 실측(숏폼 보고서)에서 네 절에 거의 그대로 실렸던 문장.
_MARKET = (
    "글로벌 숏폼 시장은 2021년 432억 달러에서 2026년 1,350억 달러로 "
    "연평균 25.6% 성장할 것으로 전망됨 (출처 1)"
)
_MARKET_REPHRASED = (
    "글로벌 숏폼 시장은 2021년 432억 달러에서 2026년 1,350억 달러로 "
    "연평균 25.6% 성장할 전망이며, 이는 기술 발전에 힘입은 결과임 (출처 1)"
)


def test_같은_문장이_다른_절에_실리면_중복으로_잡는다() -> None:
    pairs = duplicate_pairs([("2.1", _MARKET), ("4.2", _MARKET_REPHRASED)])
    assert pairs
    assert pairs[0].score >= DUPLICATE_THRESHOLD
    assert (pairs[0].first_ref, pairs[0].second_ref) == ("2.1", "4.2")


def test_같은_절_안의_반복은_보지_않는다() -> None:
    content = f"{_MARKET}\n\n{_MARKET_REPHRASED}"
    assert duplicate_pairs([("2.1", content)]) == []


def test_주제만_같고_내용이_다르면_잡지_않는다() -> None:
    other = "국내 숏폼 이용자는 하루 평균 78분을 시청하며 10대 비중이 가장 높음 (출처 4)"
    pairs = duplicate_pairs([("2.1", _MARKET), ("4.2", other)])
    assert [p for p in pairs if p.score >= DUPLICATE_THRESHOLD] == []


def test_앞_절이_먼저_오도록_정렬한다() -> None:
    pairs = duplicate_pairs([("4.2", _MARKET_REPHRASED), ("2.1", _MARKET)])
    assert (pairs[0].first_ref, pairs[0].second_ref) == ("2.1", "4.2")


def test_짧은_문장은_우연히_겹쳐도_무시한다() -> None:
    short = "이에 대한 대응이 필요함"
    assert duplicate_pairs([("2.1", short), ("3.1", short)]) == []


def test_없는_절을_가리키면_잡는다() -> None:
    sections = [
        ("1.1", "본 절은 서론이다."),
        ("2.1", "9.9절에서 살펴본 바와 같이 시장은 성장한다."),
    ]
    assert dangling_references(sections) == [("2.1", "없는 절을 가리킴: 9.9절")]


def test_있는_절_참조는_잡지_않는다() -> None:
    sections = [
        ("1.1", "본 절은 서론이다."),
        ("2.1", "1.1절에서 살펴본 바와 같이 시장은 성장한다."),
    ]
    assert dangling_references(sections) == []


def test_장은_제N장_표기만_참조로_본다() -> None:
    """실측 오탐: '추론 보드 10장~20장 구성'이 없는 장 참조로 잡혔다(2026-08-12)."""
    quantity = [("1.1", "추론 전용 가속 보드는 과제별 10장~20장 구성을 기준으로 산출함")]
    assert dangling_references(quantity) == []
    reference = [("1.1", "제9장에서 다룬 내용과 같음")]
    assert dangling_references(reference) == [("1.1", "없는 장을 가리킴: 제9장")]


def test_첫_절의_후방참조는_가리킬_대상이_없다() -> None:
    sections = [("1.1", "앞서 살펴본 바와 같이 시장은 빠르게 성장하고 있음"), ("2.1", "본론이다.")]
    assert dangling_references(sections) == [("1.1", "첫 절인데 앞을 가리킴: 앞서")]


def test_경고는_뒤_절에_붙고_건수로_심각도를_가른다() -> None:
    dup = [
        "글로벌 숏폼 시장은 2021년 432억 달러에서 2026년 1,350억 달러로 연평균 25.6% 성장 전망임",
        "인스타그램은 2024년 4월 기준 MAU 20억 명으로 전년 대비 25.3% 증가율을 기록함",
        "틱톡은 2024년 4월 기준 MAU 15억 8,200만 명을 기록하며 높은 인기를 유지하고 있음",
    ]
    sections = [("2.1", "\n\n".join(dup)), ("4.2", "\n\n".join(dup))]
    findings = cross_section_findings(sections)
    dup_findings = [f for f in findings if f["category"] == "절 간 중복"]
    assert len(dup_findings) == 1
    assert dup_findings[0]["section_ref"] == "4.2"
    assert dup_findings[0]["chapter_number"] == 4
    assert dup_findings[0]["severity"] == "warning"
    assert "2.1" in dup_findings[0]["detail"]


def test_중복이_적으면_경고하지_않는다() -> None:
    sections = [("2.1", _MARKET), ("4.2", _MARKET_REPHRASED)]
    assert [f for f in cross_section_findings(sections) if f["category"] == "절 간 중복"] == []


def test_빈_보고서에도_터지지_않는다() -> None:
    assert duplicate_pairs([]) == []
    assert dangling_references([]) == []
    assert cross_section_findings([]) == []


def test_없는_장_이름을_가리키면_잡는다() -> None:
    # 2026-08-28 v7 실측: 2.5절이 "시사점 장에서 전개"라 썼는데 그런 장이 없었다.
    sections = [("2.5", "구체적 대응은 시사점 장에서 전개한다. " * 2)]
    titles = ["서론", "EU 규제 동향", "미국 규제 동향", "일본 및 아시아 동향"]
    found = dangling_references(sections, titles)
    assert any("없는 장 이름" in why and "시사점" in why for _ref, why in found)


def test_장_제목에_있는_이름은_잡지_않는다() -> None:
    sections = [("2.5", "구체적 대응은 시사점 장에서 전개한다.")]
    titles = ["서론", "결론 및 시사점"]
    assert dangling_references(sections, titles) == []


def test_상대_지시어와_일반_명사는_잡지_않는다() -> None:
    # "다음 장에서"는 상대 지시, "시장에서"는 붙여 쓴 일반 명사다.
    sections = [("1.1", "다음 장에서 다룬다. 국내 시장에서 경쟁이 심화되고 있다.")]
    assert dangling_references(sections, ["서론", "본론"]) == []


def test_장_제목이_없으면_이름형_판정을_건너뛴다() -> None:
    sections = [("2.5", "시사점 장에서 전개한다.")]
    assert dangling_references(sections) == []


def test_시간_의미의_앞서는_후방참조가_아니다() -> None:
    # "그에 앞서 2030년"은 시간 서술이다(2026-08-28 v7 1.1절 오탐 실측).
    sections = [("1.1", "최종 시점은 2050년이며, 그에 앞서 2030년 중간 이행률을 권고함.")]
    assert dangling_references(sections) == []
    flagged = dangling_references([("1.1", "앞서 살펴본 바와 같이 규제가 강화되고 있다.")])
    assert any("앞을 가리킴" in why for _ref, why in flagged)
