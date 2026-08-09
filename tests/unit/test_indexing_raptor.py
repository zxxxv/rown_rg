"""RAPTOR 빌더의 순수 로직 검증 — 클러스터링 결정성·라벨 그룹핑·깊이 매핑.

DB·LLM·임베딩이 필요한 build() 경로는 통합 테스트 몫이다.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import numpy as np
import pytest

from src.core.config import settings
from src.services.indexing.raptor import (
    DEPTH_LEVELS,
    MAX_CLUSTERS_PER_LEVEL,
    RaptorBuilder,
    _Node,
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


class TestSummarizeLevelConcurrency:
    """클러스터 요약 병렬화 — 인덱싱 대기의 대부분이 이 루프였다.

    예타 런 실측(2026-08-10): L1 192 + L2 32 = 224콜을 순차로 돌려 10~15분.
    """

    @pytest.mark.asyncio
    async def test_runs_concurrently_and_preserves_order(self, monkeypatch):
        builder = RaptorBuilder.__new__(RaptorBuilder)  # __init__ 우회(DB·LLM 불필요)
        monkeypatch.setattr(settings, "raptor_summary_concurrency", 4)
        live = 0
        peak = 0

        async def _fake(project_id, level, members, *, user_id=None):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            live -= 1
            return _Node(None, f"요약{members[0].content}", [0.0], [])

        monkeypatch.setattr(builder, "_summarize_cluster", _fake, raising=False)
        current = [_Node(None, str(i), [0.0], []) for i in range(8)]
        groups = [[i] for i in range(8)]

        nodes = await builder._summarize_level(
            uuid4(), 1, current, groups, user_id=None, on_progress=None
        )

        assert [n.content for n in nodes] == [f"요약{i}" for i in range(8)]  # 순서 보존
        assert peak > 1  # 실제로 동시에 돌았다
        assert peak <= 4  # 상한을 넘지 않는다

    @pytest.mark.asyncio
    async def test_one_failure_drops_only_that_cluster(self, monkeypatch):
        builder = RaptorBuilder.__new__(RaptorBuilder)
        monkeypatch.setattr(settings, "raptor_summary_concurrency", 4)

        async def _fake(project_id, level, members, *, user_id=None):
            if members[0].content == "2":
                raise RuntimeError("요약 실패")
            return _Node(None, members[0].content, [0.0], [])

        monkeypatch.setattr(builder, "_summarize_cluster", _fake, raising=False)
        current = [_Node(None, str(i), [0.0], []) for i in range(4)]
        nodes = await builder._summarize_level(
            uuid4(), 1, current, [[i] for i in range(4)], user_id=None, on_progress=None
        )
        # RAPTOR는 인용 불가한 배경 맥락 — 하나 때문에 트리 전체를 잃지 않는다.
        assert [n.content for n in nodes] == ["0", "1", "3"]
