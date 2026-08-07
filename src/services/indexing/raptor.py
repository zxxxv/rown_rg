"""RAPTOR 요약 트리 빌더 — 의미 클러스터링(A안) 기반, 구조 정렬(B안) 금지.

인덱싱이 끝난 leaf 청크(level 0 = chunks 테이블)를 임베딩 공간에서 클러스터링하고,
클러스터마다 LLM 요약 1개를 만들어 raptor_nodes(level 1..N)에 적재한다. 상위 레벨은
직전 레벨 요약 노드들을 다시 클러스터링해 쌓는다.

- 트리 깊이는 작성 깊이(projects.depth_mode)가 결정한다 — depth_mode의 첫 실소비자.
- 요약 노드는 검색에서 '배경 맥락'으로만 쓰인다(인용 불가) — 인용 무결성은
  leaf 청크 계약 그대로 유지된다.
- 빌드 실패는 호출부(stages)가 삼킨다 — RAPTOR는 품질 부스터지 필수 경로가 아니다.

클러스터링은 L2 정규화 임베딩(BGE-M3 계약)에 대한 numpy k-means(코사인≡유클리드).
scikit-learn 의존을 새로 들이지 않기 위한 자체 구현이며, 시드 고정으로 결정적이다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
import structlog
from sqlalchemy import delete, select, text

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.config import settings
from src.db.models.raptor_node import RaptorNode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.clients.embedding_client import EmbeddingClient

logger = structlog.get_logger(__name__)

# 작성 깊이 → 트리 레벨 수. outline_only는 본문 검색 자체가 얕아 스킵.
DEPTH_LEVELS: dict[str, int] = {
    "outline_only": 0,
    "standard": 1,
    "full_report": 2,
    "deep_dive": 3,
}

CLUSTER_TARGET_SIZE = 6  # 클러스터당 목표 노드 수 — k = ceil(n / TARGET)
# 레벨당 LLM 요약 호출 상한 — 순수 폭주 방지용. k = ceil(N/6) 공식이 지배해야
# 클러스터가 소주제 단위(목표 6청크)로 분화된다: 12였을 때 500청크가 12덩어리
# (클러스터당 42개)로 뭉개져 요약이 전부 일반론으로 수렴했다(2026-08-06 실측).
# 192면 ~1,150청크(자료 ~60건)까지 목표 6이 유지되고, 비용 상한은 레벨당
# ~$0.4(3.1-flash-lite) — 캡이 물리는 규모가 되면 값이 아니라 트리 전략을 재검토할 것.
MAX_CLUSTERS_PER_LEVEL = 192
MIN_NODES_TO_CLUSTER = 4  # 이보다 적으면 요약할 의미가 없어 트리 성장 중단
SUMMARY_MAX_TOKENS = 700
_KMEANS_ITERS = 25
_KMEANS_SEED = 42
_MAX_CLUSTER_INPUT_CHARS = 12_000  # 요약 프롬프트에 넣을 클러스터 본문 상한

# 요약에 구체 수치를 싣지 않는다(2026-08-05): 요약은 인용 불가 '배경 맥락'인데
# 수치가 실리면 작성기가 그 수치를 근거 삼아 출처 없는 통계가 본문에 번진다
# (숏폼 실측: '[배경자료 제공됨]' 마커 오염의 원천). 수치는 원문 청크([n] 인용 가능)의 몫.
_SUMMARY_SYSTEM = (
    "너는 보고서 근거 자료를 압축하는 요약가다. 주어진 발췌들의 핵심 논지·경향·"
    "쟁점을 한국어로 촘촘하게 요약하라. 구체적인 수치·통계·연도 값은 요약에 싣지 말고"
    " '급성장', '과반' 같은 정성 표현으로 바꿔라. 새로운 주장을 만들지 말고, "
    "출처 표기나 서론 없이 요약 본문만 출력하라."
)


def kmeans_cosine(vectors: np.ndarray, k: int, *, seed: int = _KMEANS_SEED) -> list[int]:
    """L2 정규화 벡터에 대한 결정적 k-means — 라벨 리스트를 돌려준다.

    정규화 벡터에서는 유클리드 최근접 == 코사인 최근접이므로 일반 k-means로 충분.
    빈 클러스터는 중심에서 가장 먼 점을 재배치해 살린다(요약 품질 안정).
    """
    n = len(vectors)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    centers = vectors[rng.choice(n, size=k, replace=False)]
    labels = np.zeros(n, dtype=int)
    for _ in range(_KMEANS_ITERS):
        # (n, k) 거리 행렬 — 정규화 벡터라 내적이 곧 유사도
        sims = vectors @ centers.T
        new_labels = np.argmax(sims, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for ci in range(k):
            members = vectors[labels == ci]
            if len(members) == 0:
                # 빈 클러스터: 자기 중심과 가장 덜 닮은 점을 씨앗으로 재배치
                farthest = int(np.argmin(np.max(vectors @ centers.T, axis=1)))
                centers[ci] = vectors[farthest]
                labels[farthest] = ci
            else:
                center = members.mean(axis=0)
                norm = np.linalg.norm(center)
                centers[ci] = center / norm if norm > 0 else center
    return labels.tolist()


def group_by_label(labels: list[int]) -> list[list[int]]:
    """라벨 → 클러스터별 인덱스 묶음(라벨 오름차순, 빈 클러스터 제외)."""
    groups: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        groups.setdefault(label, []).append(idx)
    return [groups[label] for label in sorted(groups)]


class _Node:
    """빌드 중간 표현 — leaf 청크와 요약 노드를 같은 모양으로 다룬다."""

    __slots__ = ("id", "content", "embedding", "leaf_chunk_ids")

    def __init__(
        self, id: UUID | None, content: str, embedding: list[float], leaf_chunk_ids: list[UUID]
    ) -> None:
        self.id = id  # 요약 노드의 raptor_nodes.id (leaf는 None)
        self.content = content
        self.embedding = embedding
        self.leaf_chunk_ids = leaf_chunk_ids


class RaptorBuilder:
    """프로젝트 1건의 RAPTOR 트리를 (재)빌드한다 — 기존 트리는 지우고 새로 쌓는다."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        embedder: EmbeddingClient,
        client: LLMClient | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._embedder = embedder
        self._client = client or get_llm_client()
        self._model = model or settings.raptor_model

    async def build(
        self,
        project_id: UUID,
        *,
        depth_mode: str,
        user_id: UUID | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        """트리를 빌드하고 생성한 요약 노드 수를 돌려준다 (0 = 스킵/불충분).

        on_progress는 층·클러스터 단위 진행 라벨을 받는 콜백(진행 UI용) — 수 분짜리
        요약 루프가 밖에서 멈춘 것처럼 보이지 않게 한다. 예외는 던지지 않는 함수여야 한다.
        """
        levels = DEPTH_LEVELS.get(depth_mode, 1)
        if levels <= 0:
            logger.info("raptor.skipped", project_id=str(project_id), depth_mode=depth_mode)
            return 0

        current = await self._load_leaves(project_id)
        if len(current) < MIN_NODES_TO_CLUSTER:
            logger.info("raptor.too_few_chunks", project_id=str(project_id), n=len(current))
            return 0

        # 재실행 대비: 이전 트리 제거 (자식은 FK CASCADE)
        async with self._session_maker() as session:
            await session.execute(delete(RaptorNode).where(RaptorNode.project_id == project_id))
            await session.commit()

        created = 0
        for level in range(1, levels + 1):
            if len(current) < MIN_NODES_TO_CLUSTER:
                break
            vectors = np.asarray([n.embedding for n in current], dtype=np.float32)
            k = min(-(-len(current) // CLUSTER_TARGET_SIZE), MAX_CLUSTERS_PER_LEVEL)
            if k <= 1 and level > 1:
                break  # 더 묶어봐야 요약 1개 — 직전 레벨이 이미 그 역할을 한다
            groups = group_by_label(kmeans_cosine(vectors, k))
            if on_progress:
                on_progress(f"배경 요약 {level}층 · 클러스터 {len(groups)}개 요약 시작")
            next_nodes: list[_Node] = []
            for gi, member_idxs in enumerate(groups, start=1):
                members = [current[i] for i in member_idxs]
                node = await self._summarize_cluster(project_id, level, members, user_id=user_id)
                next_nodes.append(node)
                created += 1
                if on_progress and (gi % 10 == 0 or gi == len(groups)):
                    on_progress(f"배경 요약 {level}층 · {gi}/{len(groups)} 클러스터")
            # 굵기 자가 보고 — 클러스터당 평균이 목표(6)를 크게 넘으면 캡이 물려
            # 요약이 일반론으로 뭉개지고 있다는 신호다(2026-08-06 캡 12 실측 사고).
            avg_size = round(sum(len(g) for g in groups) / max(1, len(groups)), 1)
            logger.info(
                "raptor.level_built",
                project_id=str(project_id),
                level=level,
                n_clusters=len(next_nodes),
                avg_cluster_size=avg_size,
                capped=k >= MAX_CLUSTERS_PER_LEVEL,
            )
            current = next_nodes
        return created

    async def _load_leaves(self, project_id: UUID) -> list[_Node]:
        """content 트랙 leaf 청크(임베딩 보유분)를 빌드 입력으로 로드."""
        async with self._session_maker() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT id, content, embedding
                        FROM chunks
                        WHERE project_id = :project_id
                          AND track = 'content'
                          AND embedding IS NOT NULL
                        ORDER BY source_id, chunk_index
                        """
                    ),
                    {"project_id": str(project_id)},
                )
            ).all()
        nodes: list[_Node] = []
        for row in rows:
            embedding = row.embedding
            if isinstance(embedding, str):  # asyncpg가 vector를 텍스트로 줄 수 있다
                embedding = [float(x) for x in embedding.strip("[]").split(",")]
            nodes.append(_Node(None, row.content, list(embedding), [row.id]))
        return nodes

    async def _summarize_cluster(
        self,
        project_id: UUID,
        level: int,
        members: list[_Node],
        *,
        user_id: UUID | None,
    ) -> _Node:
        """클러스터 1개 → LLM 요약 → 임베딩 → raptor_nodes 적재 → 중간 노드 반환."""
        joined = "\n\n---\n\n".join(m.content for m in members)
        request = CompletionRequest(
            messages=[Message(role="user", content=joined[:_MAX_CLUSTER_INPUT_CHARS])],
            model=self._model,
            system=_SUMMARY_SYSTEM,
            temperature=0.2,
            max_tokens=SUMMARY_MAX_TOKENS,
            cache_key=None,
        )
        with token_context(user_id=user_id, project_id=project_id, operation="raptor.summary"):
            response = await self._client.complete(request)
        summary = response.content.strip()
        embedding = (await self._embedder.embed(summary)).embedding
        leaf_ids = [cid for m in members for cid in m.leaf_chunk_ids]

        async with self._session_maker() as session:
            node = RaptorNode(
                project_id=project_id,
                level=level,
                summary=summary,
                embedding=embedding,
                chunk_ids=leaf_ids,
                metadata_={"n_members": len(members)},
            )
            session.add(node)
            await session.flush()
            node_id = node.id
            # 직전 레벨 요약 노드들을 자식으로 연결 (leaf 청크는 chunk_ids로만 연결)
            child_ids = [m.id for m in members if m.id is not None]
            if child_ids:
                await session.execute(
                    select(RaptorNode.id).where(RaptorNode.id.in_(child_ids))
                )  # 존재 검증 겸 락 회피용 조회
                for child in (
                    await session.execute(select(RaptorNode).where(RaptorNode.id.in_(child_ids)))
                ).scalars():
                    child.parent_id = node_id
            await session.commit()
        return _Node(node_id, summary, list(embedding), leaf_ids)


def build_raptor_builder() -> RaptorBuilder:
    """실배선 팩토리 — stages가 사용. 테스트는 모듈 전역 주입으로 교체한다."""
    from src.clients.embedding_factory import get_embedding_client
    from src.db.session import async_session_maker

    return RaptorBuilder(async_session_maker, get_embedding_client())
