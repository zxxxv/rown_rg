"""절 간 중복·떠 있는 참조 검출 — 실측 사례를 그대로 시험한다."""

from __future__ import annotations

from src.services.qa.cross_section import (
    DUPLICATE_THRESHOLD,
    dangling_references,
    duplicate_pairs,
    pointer_restatements,
    source_mismatch_pairs,
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


# ---- 참조 포인터 제외 (2026-08-29 철강 정독 오탐 수리) ----

_POINTER = (
    "'협력모델'의 개념(수평적·수직적 협력체계)과 지정 요건 및 세부 유형은 1.1절 참조(출처 31)"
)


def test_포인터_문장끼리는_중복으로_잡지_않는다() -> None:
    # 실측 오탐: 4.2와 1.2가 같은 위임 문장을 갖고 있었다 — 실체는 1.1에만 있다.
    assert duplicate_pairs([("1.2", _POINTER), ("4.2", _POINTER)]) == []


def test_실내용_문장에_덧붙은_참조는_포인터가_아니다() -> None:
    body = (
        "특구 지정 시 연구개발·인프라·인력·투자 지원이 집중 추진되고 수소환원제철 실증을"
        " 촉진하는 플랫폼으로 기능할 것으로 기대됨(1.1절 참조)"
    )
    pairs = duplicate_pairs([("1.1", body), ("5.1", body)])
    assert pairs and pairs[0].score >= DUPLICATE_THRESHOLD


# ---- 중복 출처 불일치 (2026-08-29 철강 정독: 같은 문장, 절마다 출처 4벌) ----

_POHANG_A = (
    "포스코는 포항제철소에 수소환원제철 개발센터를 개소하고 2030년 상용 기술 개발"
    " 완료를 목표로 연구개발을 지속하고 있음(출처 8)"
)
_POHANG_B = (
    "포스코는 포항제철소에 수소환원제철 개발센터를 개소하고 2030년 상용 기술 개발"
    " 완료를 목표로 연구개발을 지속하고 있음(출처 31)"
)


def test_같은_문장에_다른_출처면_불일치로_잡는다() -> None:
    pairs = duplicate_pairs([("4.1", _POHANG_A), ("4.3", _POHANG_B)])
    mismatches = source_mismatch_pairs(pairs)
    assert len(mismatches) == 1
    assert mismatches[0].first_sources == (8,)
    assert mismatches[0].second_sources == (31,)


def test_출처가_겹치면_불일치가_아니다() -> None:
    with_overlap = _POHANG_A.replace("(출처 8)", "(출처 8, 31)")
    pairs = duplicate_pairs([("4.1", with_overlap), ("4.3", _POHANG_B)])
    assert source_mismatch_pairs(pairs) == []


def test_한쪽에_출처가_없으면_판정하지_않는다() -> None:
    bare = _POHANG_A.replace("(출처 8)", "")
    pairs = duplicate_pairs([("4.1", bare), ("4.3", _POHANG_B)])
    assert source_mismatch_pairs(pairs) == []


def test_불일치_소견이_경고_행으로_나온다() -> None:
    findings = cross_section_findings([("4.1", _POHANG_A), ("4.3", _POHANG_B)])
    rows = [f for f in findings if f["category"] == "중복 출처 불일치"]
    assert len(rows) == 1
    assert rows[0]["section_ref"] == "4.3"
    assert "출처 8" in rows[0]["detail"] and "출처 31" in rows[0]["detail"]


# ---- 참조 후 재서술 (2026-08-29 철강 5.1 실측) ----


def test_참조로_넘긴_절을_다시_쓰면_잡는다() -> None:
    origin = (
        "철스크랩은 전기로 기반 저탄소 철강 생산의 핵심 원료로, 정부는 전문기업 육성을"
        " 통해 품질 향상과 안정적 공급체계 구축을 추진함(출처 31)"
    )
    restated = (
        "동 특별법 및 시행령의 제정·시행 경과는 1.1절 참조.\n\n"
        "철스크랩은 전기로 기반 저탄소 철강 생산의 핵심 원료로, 정부는 전문기업 육성을"
        " 통해 품질 향상과 안정적 공급체계 구축을 추진함(출처 17)"
    )
    sections = [("1.1", origin), ("5.1", restated)]
    pairs = duplicate_pairs(sections)
    assert pointer_restatements(sections, pairs) == [("5.1", "1.1", 1)]
    rows = [f for f in cross_section_findings(sections) if f["category"] == "참조 후 재서술"]
    assert len(rows) == 1 and rows[0]["section_ref"] == "5.1"


def test_참조만_하고_재서술이_없으면_잡지_않는다() -> None:
    sections = [
        ("1.1", "저탄소철강특구 지정 요건과 지원 내용은 특별법 제23조가 규정함(출처 31)"),
        (
            "5.1",
            "동 특별법 및 시행령의 제정·시행 경과는 1.1절 참조.\n\n5장 고유의 정책 평가 서술임",
        ),
    ]
    pairs = duplicate_pairs(sections)
    assert pointer_restatements(sections, pairs) == []
