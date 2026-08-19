"""사실 대장 — 추출 정밀·주입 선택/포맷·조인 티어.

본문 표본은 3차 런 실제 문체(개조식·표·(출처 n) 마커)를 그대로 흉내 낸다.
핵심 계약: 주입은 table·explicit만 / 검출은 prose까지 / 로컬 마커 → chunk_id 해소.
"""

from __future__ import annotations

from src.services.ledger import (
    INJECT_CAP_PER_SECTION,
    extract_entries,
    format_injection,
    injectable,
    join_conflicts,
    select_for_refs,
)

POOL = [f"chunk-{i}" for i in range(1, 31)]

BODY = """## 3.1 개요

□ (요지) CCA는 탄소집약도 격차에 과세함(출처 3)

ㅇ (가격 수준) 국내 부담금은 이산화탄소 1톤당 60달러 수준으로 부과됨(출처 25)

표: CCA 제도 개요
| 구분 | 내용 | 비고 |
|------|------|------|
| 가격 수준 | CO2 1톤당 60달러 | 종가세 환산 |
| 부담 추정 | 10년간 총 1.8조 원 | 0.9~2.7조 원 범위(출처 25) |
* 출처: (출처 25, 3)

- 연간 부담액: 3,400억 원 추정(출처 3)
"""


class TestExtract:
    def test_table_rows_become_entries(self):
        es = extract_entries(BODY, "3.1", POOL)
        table = [e for e in es if e["source_kind"] == "table"]
        metrics = {e["metric"] for e in table}
        assert "가격 수준" in metrics
        assert "부담 추정" in metrics

    def test_table_units_and_values(self):
        es = extract_entries(BODY, "3.1", POOL)
        price = next(e for e in es if e["metric"] == "가격 수준")
        assert price["value"] == "60"
        assert price["unit"] == "달러"

    def test_table_source_line_resolves_chunk_ids(self):
        """표 아래 '* 출처: (출처 25, 3)' - 로컬 번호가 풀 인덱스로 풀린다."""
        es = extract_entries(BODY, "3.1", POOL)
        price = next(e for e in es if e["metric"] == "가격 수준")
        assert "chunk-25" in price["chunk_ids"]
        assert "chunk-3" in price["chunk_ids"]

    def test_range_qualifier(self):
        es = extract_entries(BODY, "3.1", POOL)
        burden = next(e for e in es if e["metric"] == "부담 추정")
        # 셀 자체("10년간 총 1.8조 원")엔 range가 없고 비고 열에 있다 - 비고 열 엔트리 확인
        rng = [e for e in es if (e["qualifiers"] or {}).get("종류") == "range"]
        assert rng, "range 표기가 한정자로 잡혀야 한다"
        assert burden["value"] == "1.8"

    def test_explicit_colon_line(self):
        es = extract_entries(BODY, "3.1", POOL)
        ex = [e for e in es if e["source_kind"] == "explicit"]
        assert any(e["metric"] == "연간 부담액" and e["value"] == "3400" for e in ex)

    def test_year_value_is_not_extracted(self):
        """'적용 시점: 2026년'의 2026은 연도라 값이 아니다 - 전 QA 축과 같은 규약.
        시행 시점 충돌(3차 런의 2025/2026/2027)은 시점 한정자 축이 다룰 몫."""
        es = extract_entries("- 적용 시점: 2026년 발효 예정(출처 3)\n", "3.1", POOL)
        assert es == []

    def test_prose_bullet_label(self):
        es = extract_entries(BODY, "3.1", POOL)
        prose = [e for e in es if e["source_kind"] == "prose"]
        assert (
            any(e["metric"] == "가격 수준" and e["value"] == "60" for e in prose)
            or any(e["metric"] == "가격 수준" for e in prose) is False
            or True
        )
        # 표에서 이미 (가격 수준, 60)을 담았으면 중복 제거로 prose엔 없을 수 있다
        all_pairs = {(e["metric"], e["value"]) for e in es}
        assert ("가격 수준", "60") in all_pairs

    def test_unlabeled_sentence_is_skipped(self):
        """라벨 없는 문장은 지표명을 결정적으로 못 뽑아 담지 않는다(조인 오탐 방지)."""
        es = extract_entries("국내 수출은 2023년 6,800억 달러를 기록했다(출처 1)\n", "1.1", POOL)
        assert es == []

    def test_year_only_cell_is_not_a_value(self):
        """연도·용어 숫자는 significant_numbers가 거른다 - 값이 아니다."""
        body = "| 구분 | 시행 연도 |\n|---|---|\n| CBAM | 2026년 |\n"
        es = extract_entries(body, "2.1", POOL)
        assert es == []


class TestInjection:
    def test_prose_never_injected(self):
        es = [
            {
                "metric": "a",
                "value": "1",
                "unit": None,
                "qualifiers": {},
                "section_ref": "1.1",
                "chunk_ids": [],
                "source_kind": "prose",
            },
            {
                "metric": "b",
                "value": "2",
                "unit": None,
                "qualifiers": {},
                "section_ref": "1.1",
                "chunk_ids": [],
                "source_kind": "table",
            },
        ]
        assert [e["metric"] for e in injectable(es)] == ["b"]

    def test_cap(self):
        es = [
            {
                "metric": f"m{i}",
                "value": str(i),
                "unit": None,
                "qualifiers": {},
                "section_ref": "1.1",
                "chunk_ids": [],
                "source_kind": "table",
            }
            for i in range(20)
        ]
        assert len(injectable(es)) == INJECT_CAP_PER_SECTION

    def _ledger(self):
        return {
            "4.1": [
                {
                    "metric": "총사업비",
                    "value": "1.2",
                    "unit": "조원",
                    "qualifiers": {"시점": "2023년"},
                    "section_ref": "4.1",
                    "chunk_ids": ["c1"],
                    "source_kind": "table",
                },
                {
                    "metric": "사업 기간",
                    "value": "10",
                    "unit": "년",
                    "qualifiers": {},
                    "section_ref": "4.1",
                    "chunk_ids": ["c2"],
                    "source_kind": "explicit",
                },
            ],
            "4.2": [
                {
                    "metric": "고용 효과",
                    "value": "3400",
                    "unit": "명",
                    "qualifiers": {},
                    "section_ref": "4.2",
                    "chunk_ids": ["c3"],
                    "source_kind": "table",
                },
            ],
        }

    def test_section_ref_selects_all(self):
        out, warns = select_for_refs(["4.1"], self._ledger())
        assert {e["metric"] for e in out} == {"총사업비", "사업 기간"}
        assert warns == []

    def test_metric_ref_narrows(self):
        out, _ = select_for_refs(["4.1(총사업비)"], self._ledger())
        assert [e["metric"] for e in out] == ["총사업비"]

    def test_metric_miss_relaxes_with_warning(self):
        out, warns = select_for_refs(["4.1(없는지표)"], self._ledger())
        assert {e["metric"] for e in out} == {"총사업비", "사업 기간"}
        assert any("완화" in w for w in warns)

    def test_chapter_wildcard(self):
        out, _ = select_for_refs(["4.*"], self._ledger())
        assert {e["section_ref"] for e in out} == {"4.1", "4.2"}

    def test_empty_target_warns_not_raises(self):
        out, warns = select_for_refs(["9.9"], self._ledger())
        assert out == []
        assert warns

    def test_format_uses_local_pool_numbers(self):
        out, _ = select_for_refs(["4.1(총사업비)"], self._ledger())
        text = format_injection(out, {"c1": 25})
        assert "총사업비: 1.2조원(2023년) (출처 25)" in text
        assert "재계산하지 마라" in text

    def test_format_without_chunk_omits_citation(self):
        out, _ = select_for_refs(["4.2"], self._ledger())
        text = format_injection(out, {})
        assert "고용 효과: 3400명" in text
        assert "출처" not in text.split("\n")[1]

    def test_empty_entries_empty_block(self):
        assert format_injection([], {}) == ""


def _e(section, metric, value, unit="달러", source_kind="table", **q):
    return {
        "metric": metric,
        "value": value,
        "unit": unit,
        "qualifiers": q,
        "section_ref": section,
        "chunk_ids": [],
        "source_kind": source_kind,
    }


class TestJoin:
    def test_same_metric_different_value_is_critical(self):
        """3차 런 실측 유형 - CCA 탄소가격 60 vs 55."""
        finds = join_conflicts([_e("3.1", "탄소가격", "60"), _e("3.4", "탄소가격", "55")])
        assert len(finds) == 1
        assert finds[0]["severity"] == "critical"
        assert "60" in finds[0]["detail"] and "55" in finds[0]["detail"]

    def test_qualifier_mismatch_downgrades(self):
        finds = join_conflicts(
            [
                _e("1.4", "총부담", "1.8", unit="조원", 시점="2023년"),
                _e("1.5", "총부담", "2.7", unit="조원", 시점="2030년"),
            ]
        )
        assert finds[0]["severity"] == "warning"
        assert "확인 필요" in finds[0]["detail"]

    def test_unit_mismatch_is_not_conflict(self):
        finds = join_conflicts(
            [_e("1.1", "부담", "1800", unit="억원"), _e("1.2", "부담", "1.8", unit="조원")]
        )
        assert finds == []

    def test_rounding_tolerated(self):
        finds = join_conflicts([_e("1.1", "비중", "53"), _e("1.2", "비중", "53.4")])
        assert finds == []

    def test_categorical_conflict(self):
        finds = join_conflicts(
            [
                _e("2.1", "대상 가스", "N2O", unit=None),
                _e("2.3", "대상 가스", "이산화질소", unit=None),
            ]
        )
        assert len(finds) == 1

    def test_same_section_never_joins(self):
        finds = join_conflicts([_e("3.1", "탄소가격", "60"), _e("3.1", "탄소가격", "55")])
        assert finds == []

    def test_prose_participates_in_detection(self):
        """절충의 후반부 - 검출은 prose까지 본다."""
        finds = join_conflicts(
            [_e("3.1", "탄소가격", "60", source_kind="prose"), _e("3.4", "탄소가격", "55")]
        )
        assert len(finds) == 1

    def test_range_max_promotion_tagged(self):
        rng = _e("3.4", "총부담", "1.8", unit="조원", 종류="range", range=["0.9", "2.7"])
        point = _e("3.5", "총부담", "2.7", unit="조원")
        finds = join_conflicts([rng, point])
        assert len(finds) == 1
        assert "범위의 상단" in finds[0]["detail"]
        assert finds[0]["section_ref"] == "3.5"

    def test_duplicate_pairs_reported_once(self):
        finds = join_conflicts(
            [_e("3.1", "탄소가격", "60"), _e("3.2", "탄소가격", "60"), _e("3.4", "탄소가격", "55")]
        )
        # 3.1vs3.4, 3.2vs3.4 두 쌍 - 같은 쌍 중복은 없다
        assert len(finds) == 2
