"""RAPTOR 빌더의 순수 로직 검증 — 클러스터링 결정성·라벨 그룹핑·깊이 매핑.

DB·LLM·임베딩이 필요한 build() 경로는 통합 테스트 몫이다.
"""

from __future__ import annotations

import numpy as np

from src.services.indexing.raptor import (
    DEPTH_LEVELS,
    MAX_CLUSTERS_PER_LEVEL,
    group_by_label,
    kmeans_cosine,
)


def _normalized(rows: list[list[float]]) -> np.ndarray:
    arr = np.asarray(rows, dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


class TestKmeansCosine:
    def test_deterministic(self):
        rng = np.random.default_rng(7)
        vectors = _normalized(rng.normal(size=(30, 8)).tolist())
        assert kmeans_cosine(vectors, 4) == kmeans_cosine(vectors, 4)

    def test_separates_obvious_clusters(self):
        # 서로 직교에 가까운 두 방향 주변의 점들 — 반드시 두 그룹으로 갈린다.
        a = _normalized([[1, 0, 0.01 * i] for i in range(5)])
        b = _normalized([[0, 1, 0.01 * i] for i in range(5)])
        vectors = np.vstack([a, b])
        labels = kmeans_cosine(vectors, 2)
        assert len(set(labels[:5])) == 1
        assert len(set(labels[5:])) == 1
        assert labels[0] != labels[5]

    def test_k_capped_by_n(self):
        vectors = _normalized([[1, 0], [0, 1]])
        labels = kmeans_cosine(vectors, 10)
        assert len(labels) == 2
        assert max(labels) <= 1


class TestGroupByLabel:
    def test_groups_indices_in_order(self):
        assert group_by_label([1, 0, 1, 2]) == [[1], [0, 2], [3]]

    def test_empty(self):
        assert group_by_label([]) == []


class TestDepthMapping:
    def test_all_depth_modes_mapped(self):
        # projects.depth_mode CHECK 제약과 어휘가 일치해야 한다.
        assert set(DEPTH_LEVELS) == {"outline_only", "standard", "full_report", "deep_dive"}
        assert DEPTH_LEVELS["outline_only"] == 0  # 스킵
        assert DEPTH_LEVELS["standard"] < DEPTH_LEVELS["full_report"] < DEPTH_LEVELS["deep_dive"]

    def test_cluster_cap_positive(self):
        assert MAX_CLUSTERS_PER_LEVEL >= 1
