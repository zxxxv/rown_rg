"""scripts/eval_search.py의 지표 함수 검증 — 학계 표준 예제 + edge case."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.eval_search import (
    HitGrade,
    ScoresStore,
    _dcg,
    load_queries,
    ndcg_at_k,
    precision_at_k,
)

# ---------- _dcg (DCG 게인 공식 검증) ----------


class TestDCG:
    """``2^r - 1`` 게인 + ``log2(i + 2)`` 분모 공식의 정확성 검증."""

    def test_empty_list_returns_zero(self):
        assert _dcg([]) == 0.0

    def test_all_zero_grades_returns_zero(self):
        assert _dcg([0, 0, 0, 0]) == 0.0

    def test_single_perfect_at_rank_1(self):
        # grade=3, rank 0 → (2^3 - 1) / log2(2) = 7 / 1 = 7
        assert _dcg([3]) == pytest.approx(7.0)

    def test_single_perfect_at_rank_2(self):
        # rank 1 (0-indexed) → (2^3 - 1) / log2(3) ≈ 7 / 1.585 ≈ 4.4174
        expected = 7.0 / math.log2(3)
        assert _dcg([0, 3]) == pytest.approx(expected)

    def test_textbook_example(self):
        """학계 표준 예제: grades = [3, 2, 3, 0, 1, 2]
        DCG = 7/1 + 3/log2(3) + 7/log2(4) + 0/log2(5) + 1/log2(6) + 3/log2(7)
        """
        grades = [3, 2, 3, 0, 1, 2]
        expected = (
            7.0 / math.log2(2)
            + 3.0 / math.log2(3)
            + 7.0 / math.log2(4)
            + 0.0 / math.log2(5)
            + 1.0 / math.log2(6)
            + 3.0 / math.log2(7)
        )
        assert _dcg(grades) == pytest.approx(expected)


# ---------- ndcg_at_k ----------


class TestNDCGAtK:
    def test_empty_returns_zero(self):
        assert ndcg_at_k([]) == 0.0

    def test_all_zero_returns_zero(self):
        # IDCG도 0이라 0/0이지만 명시적으로 0 반환.
        assert ndcg_at_k([0, 0, 0, 0]) == 0.0

    def test_already_ideal_order_returns_one(self):
        # 내림차순 정렬된 입력 → actual = ideal → NDCG = 1.0
        assert ndcg_at_k([3, 3, 2, 2, 1, 0]) == pytest.approx(1.0)

    def test_worst_order_is_lower(self):
        # 오름차순 정렬된 입력 → 최저 ranking 품질
        worst = ndcg_at_k([0, 1, 2, 2, 3, 3])
        best = ndcg_at_k([3, 3, 2, 2, 1, 0])
        assert worst < best
        assert 0.0 < worst < 1.0

    def test_single_grade_returns_one(self):
        # 항목 1개는 자기 자신이 ideal → NDCG = 1.0
        assert ndcg_at_k([2]) == pytest.approx(1.0)

    def test_k_truncates_input(self):
        # k=3이면 [3,3,3,0,0] 앞 3개만 → 모두 동등 → NDCG=1.0
        assert ndcg_at_k([3, 3, 3, 0, 0], k=3) == pytest.approx(1.0)
        # 같은 입력 k=5는 끝의 0들도 포함하지만 IDCG도 같은 식으로 계산되므로 여전히 1.0
        assert ndcg_at_k([3, 3, 3, 0, 0], k=5) == pytest.approx(1.0)

    def test_binary_grades(self):
        # 이진 (0/1) 게인도 동일 공식 — `2^1 - 1 = 1`, `2^0 - 1 = 0`이라 일반 DCG 공식 동치.
        # 모든 1이 앞에 있을 때 NDCG=1.0
        assert ndcg_at_k([1, 1, 1, 0, 0]) == pytest.approx(1.0)

    def test_known_example_calculation(self):
        """grades = [2, 3, 3, 1, 2] 의 NDCG@5 수동 계산 검증."""
        grades = [2, 3, 3, 1, 2]
        dcg = 3 / 1 + 7 / math.log2(3) + 7 / math.log2(4) + 1 / math.log2(5) + 3 / math.log2(6)
        idcg = 7 / 1 + 7 / math.log2(3) + 3 / math.log2(4) + 3 / math.log2(5) + 1 / math.log2(6)
        expected = dcg / idcg
        assert ndcg_at_k(grades) == pytest.approx(expected)


# ---------- precision_at_k ----------


class TestPrecisionAtK:
    def test_empty_returns_zero(self):
        assert precision_at_k([]) == 0.0

    def test_all_relevant(self):
        # 임계치 default = 2. [2,3,2,3] 4개 모두 ≥2.
        assert precision_at_k([2, 3, 2, 3]) == pytest.approx(1.0)

    def test_none_relevant(self):
        # 모두 1 → ≥2 없음 → 0.0
        assert precision_at_k([0, 1, 1, 0]) == pytest.approx(0.0)

    def test_half_relevant(self):
        # 10개 중 5개가 ≥2 → 0.5
        grades = [3, 2, 2, 2, 2, 1, 0, 1, 0, 1]
        assert precision_at_k(grades) == pytest.approx(0.5)

    def test_custom_threshold(self):
        # threshold=1 — ≥1인 항목 비율
        assert precision_at_k([0, 1, 2, 0], threshold=1) == pytest.approx(0.5)

    def test_k_smaller_than_input(self):
        # k=3이면 앞 3개만 본다 — [3,2,1] → ≥2 두 개 → 2/3
        assert precision_at_k([3, 2, 1, 3, 3], k=3) == pytest.approx(2 / 3)


# ---------- ScoresStore (resumable persistence) ----------


class TestScoresStore:
    def test_set_then_get_roundtrip(self, tmp_path: Path):
        store = ScoresStore(tmp_path / "scores.json")
        store.set("q01", "hybrid", "chunk-abc", snippet="내용", grade=2)
        result = store.get("q01", "hybrid", "chunk-abc")
        assert result is not None
        assert result.grade == 2
        assert result.snippet == "내용"

    def test_get_missing_returns_none(self, tmp_path: Path):
        store = ScoresStore(tmp_path / "scores.json")
        assert store.get("q99", "semantic_only", "nonexistent") is None

    def test_save_then_reload_preserves_state(self, tmp_path: Path):
        path = tmp_path / "scores.json"
        s1 = ScoresStore(path)
        s1.set("q01", "hybrid", "chunk-1", snippet="a", grade=3)
        s1.set("q01", "keyword_only", "chunk-1", snippet="a", grade=1)
        s1.save()

        # 새 인스턴스에서 같은 파일을 로드 → 동일 데이터 복원
        s2 = ScoresStore(path)
        assert s2.get("q01", "hybrid", "chunk-1").grade == 3
        assert s2.get("q01", "keyword_only", "chunk-1").grade == 1

    def test_atomic_save_no_partial_state(self, tmp_path: Path):
        path = tmp_path / "scores.json"
        store = ScoresStore(path)
        store.set("q01", "hybrid", "chunk-1", snippet="a", grade=3)
        store.save()
        # 저장 후 .tmp 파일이 남지 않아야 함
        assert not (tmp_path / "scores.json.tmp").exists()
        # JSON이 유효한지 직접 확인
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["q01"]["hybrid"]["chunk-1"]["grade"] == 3

    def test_grades_for_orders_by_chunk_ids(self, tmp_path: Path):
        store = ScoresStore(tmp_path / "scores.json")
        store.set("q01", "hybrid", "c1", snippet="", grade=3)
        store.set("q01", "hybrid", "c2", snippet="", grade=1)
        store.set("q01", "hybrid", "c3", snippet="", grade=2)
        # 검색 결과 순서대로 grade 추출
        assert store.grades_for("q01", "hybrid", ["c1", "c2", "c3"]) == [3, 1, 2]
        # 일부 미채점 → 0 fill
        assert store.grades_for("q01", "hybrid", ["c1", "missing", "c2"]) == [3, 0, 1]

    def test_coverage_counts_rated_and_total(self, tmp_path: Path):
        store = ScoresStore(tmp_path / "scores.json")
        store.set("q01", "hybrid", "c1", snippet="", grade=3)
        store.set("q01", "hybrid", "c2", snippet="", grade=0)
        rated, total = store.coverage("q01", "hybrid", ["c1", "c2", "c3", "c4"])
        assert rated == 2
        assert total == 4

    def test_overwrite_replaces_previous_grade(self, tmp_path: Path):
        store = ScoresStore(tmp_path / "scores.json")
        store.set("q01", "hybrid", "c1", snippet="a", grade=1)
        store.set("q01", "hybrid", "c1", snippet="a", grade=3)
        assert store.get("q01", "hybrid", "c1").grade == 3


# ---------- load_queries (YAML 파싱) ----------


class TestLoadQueries:
    def test_minimal_yaml_parses(self, tmp_path: Path):
        path = tmp_path / "q.yaml"
        path.write_text(
            """
queries:
  - id: q01
    text: "쿼리1"
    category: 경제성
  - id: q02
    text: "쿼리2"
""",
            encoding="utf-8",
        )
        qs = load_queries(path)
        assert len(qs) == 2
        assert qs[0].id == "q01"
        assert qs[0].text == "쿼리1"
        assert qs[0].category == "경제성"
        # 카테고리 미지정 → 기본값
        assert qs[1].category == "uncategorized"

    def test_empty_queries_list_raises(self, tmp_path: Path):
        path = tmp_path / "q.yaml"
        path.write_text("queries: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="비어있음"):
            load_queries(path)

    def test_missing_queries_key_raises(self, tmp_path: Path):
        path = tmp_path / "q.yaml"
        path.write_text("other: 1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="queries"):
            load_queries(path)

    def test_duplicate_id_raises(self, tmp_path: Path):
        path = tmp_path / "q.yaml"
        path.write_text(
            """
queries:
  - id: q01
    text: "a"
  - id: q01
    text: "b"
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="중복된 query id"):
            load_queries(path)


# ---------- HitGrade dataclass roundtrip ----------


class TestHitGrade:
    def test_roundtrip_via_json(self, tmp_path: Path):
        hg = HitGrade(chunk_id="abc", grade=2, snippet="내용", rated_at="2026-05-20T03:00:00+00:00")
        serialized = json.dumps(hg.__dict__, ensure_ascii=False)
        restored = HitGrade(**json.loads(serialized))
        assert restored == hg
