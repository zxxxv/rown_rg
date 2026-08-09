"""Integration tests covering parsing → chunking → indexing → retrieval → reranking.

Covers what unit tests cannot:

- Module-to-module data shape compatibility on real parser output.
- DB-side JSONB roundtrip for chunk metadata.
- Real-embedding HNSW + real-tokenizer pgroonga behavior on indexed data.
- Track filter isolation across sources.
- Reranker metadata preservation (original_score / original_score_source)
  on real search hits.

Markers:

- ``integration``: real DB connection required.
- ``slow``: parser/embedder/reranker cold loads dominate.
- ``requires_model``: skipped if ``./models/bge-m3-onnx-int8/`` or
  ``./models/bge-reranker-v2-m3-onnx-int8/`` is missing — run the
  setup scripts in ``scripts/setup_bge_*.py`` first.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.clients.embedding_client import BgeM3Client
from src.clients.parser.registry import ParserRegistry
from src.clients.reranker_client import BgeRerankerV2M3Client
from src.db.models.chunk import Chunk as ChunkModel
from src.db.models.library_node import LibraryNode
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.user import User
from src.infrastructure.auth.password_handler import hash_password
from src.services.indexing._chunking import ChunkingService
from src.services.indexing.vector import SourceInput, VectorIndexingService
from src.services.retrieval._keyword import KeywordSearchClient
from src.services.retrieval._reranking import rerank_hits
from src.services.retrieval._semantic import SemanticSearchClient
from src.services.retrieval.hybrid import HybridSearchClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.slow]


SAMPLE_HWPX = Path("tests/fixtures/sample.hwpx")
BGE_M3_MODEL = Path("./models/bge-m3-onnx-int8/model.onnx")
RERANKER_MODEL = Path("./models/bge-reranker-v2-m3-onnx-int8/model.onnx")


@pytest.fixture(scope="session")
def parser_registry() -> ParserRegistry:
    return ParserRegistry()


@pytest.fixture(scope="session")
def embedder() -> BgeM3Client:
    if not BGE_M3_MODEL.exists():
        pytest.skip("BGE-M3 ONNX 모델 없음 — scripts/setup_bge_m3.py 먼저 실행")
    return BgeM3Client()


@pytest.fixture(scope="session")
def reranker() -> BgeRerankerV2M3Client:
    if not RERANKER_MODEL.exists():
        pytest.skip("Reranker ONNX 모델 없음 — scripts/setup_bge_reranker.py 먼저 실행")
    return BgeRerankerV2M3Client()


@pytest.fixture(scope="session")
def chunking_service(embedder: BgeM3Client) -> ChunkingService:
    return ChunkingService(embedder)


async def _seed_project_and_library_node(
    session: AsyncSession,
    *,
    node_name: str = "sample.hwpx",
) -> tuple[Project, LibraryNode]:
    """User → Project → LibraryNode를 시드. project_id·library_node_id 반환."""
    user = User(
        email=f"int-{uuid4().hex[:6]}@test.com",
        name="int",
        role="worker",
        password_hash=hash_password("Integration123!@"),
        is_active=True,
    )
    session.add(user)
    await session.flush()
    project = Project(title="int-project", topic="integration", owner_id=user.id)
    session.add(project)
    node = LibraryNode(name=node_name, type="file", file_path=str(SAMPLE_HWPX))
    session.add(node)
    await session.commit()
    await session.refresh(project)
    await session.refresh(node)
    return project, node


async def _index_sample(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    project_id,
    library_node_id,
    parser_registry: ParserRegistry,
    chunking_service: ChunkingService,
    embedder: BgeM3Client,
    track: str = "content",
):
    """sample.hwpx를 인덱싱 — 헬퍼로 묶어 각 테스트가 한 줄로 부른다."""
    svc = VectorIndexingService(
        parser_registry=parser_registry,
        chunking_service=chunking_service,
        embedding_client=embedder,
        session_maker=session_maker,
    )
    return await svc.index_source(
        SourceInput(
            project_id=project_id,
            source_type="library",
            file_path=SAMPLE_HWPX,
            library_node_id=library_node_id,
            track=track,  # type: ignore[arg-type]
        )
    )


# ===========================================================================
# 모듈 간 인터페이스 정합
# ===========================================================================


@pytest.mark.skipif(not SAMPLE_HWPX.exists(), reason="sample.hwpx 미존재")
class TestModuleInterfaces:
    """단위 테스트가 mock으로 가린 모듈 간 데이터 형식 정합을 실제 파일로 검증."""

    @pytest.mark.requires_model
    async def test_parser_output_drives_chunker(
        self,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
    ):
        """파서 출력 → 청커 입력 정합. metadata 필수 키 + chunk_index 연속성."""
        parse_result = await parser_registry.parse(SAMPLE_HWPX)
        assert isinstance(parse_result.markdown, str)
        assert len(parse_result.markdown.strip()) > 0

        source_id = uuid4()
        chunks = await chunking_service.chunk_markdown(parse_result.markdown, source_id)

        assert len(chunks) > 0, "청크 생성 0개 — 파서 출력이 청커 입력으로 부적합"
        assert all(c.source_id == source_id for c in chunks)
        for chunk in chunks:
            # 인덱서가 의존하는 필수 메타데이터 키
            assert "header_path" in chunk.metadata
            assert "chunk_type" in chunk.metadata
            assert chunk.metadata["chunk_type"] in ("text", "table")
        # chunk_index 연속성 — add_all 시 순서 보존 기대.
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    async def test_chunk_metadata_jsonb_roundtrip(
        self,
        test_session: AsyncSession,
        test_session_maker: async_sessionmaker,
    ):
        """Chunk.metadata(JSONB)가 dict 직렬화/역직렬화 손실 없이 보존."""
        project, node = await _seed_project_and_library_node(test_session)

        source = ProjectSource(
            project_id=project.id,
            source_type="library",
            library_node_id=node.id,
        )
        test_session.add(source)
        await test_session.commit()
        await test_session.refresh(source)

        # 복합 metadata — list, nested dict, int, bool, 한국어까지 모두 검증
        complex_metadata = {
            "header_path": ["섹션 A", "하위 1"],
            "chunk_type": "text",
            "token_count_estimate": 42,
            "has_numbers": True,
            "nested": {"key": "값", "list": [1, 2, 3]},
        }

        async with test_session_maker() as s:
            chunk = ChunkModel(
                project_id=project.id,
                source_id=source.id,
                chunk_index=0,
                content="테스트 내용",
                track="content",
                metadata_=complex_metadata,
                embedding=[0.0] * 1024,
            )
            s.add(chunk)
            await s.commit()
            chunk_id = chunk.id

        # 새 세션에서 조회 — JSONB 직렬화·역직렬화가 dict를 그대로 복원하는지
        async with test_session_maker() as s:
            loaded = (
                await s.execute(select(ChunkModel).where(ChunkModel.id == chunk_id))
            ).scalar_one()

        assert loaded.metadata_ == complex_metadata


# ===========================================================================
# 전체 파이프라인
# ===========================================================================


@pytest.mark.skipif(not SAMPLE_HWPX.exists(), reason="sample.hwpx 미존재")
@pytest.mark.requires_model
class TestFullPipeline:
    """파일 → 인덱싱 → 검색 → 리랭킹 end-to-end."""

    async def test_hwpx_indexes_then_searchable_and_rerankable(
        self,
        test_session: AsyncSession,
        test_session_maker: async_sessionmaker,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        embedder: BgeM3Client,
        reranker: BgeRerankerV2M3Client,
    ):
        """sample.hwpx 인덱싱 → 자기 자신 쿼리 → hit 포함 → 리랭킹 score_source 갱신."""
        project, node = await _seed_project_and_library_node(test_session)
        result = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
        )
        assert result.chunks_created > 0

        # 첫 청크의 일부를 쿼리로 → semantic 코사인 ≈ 1, keyword도 단어 일치
        async with test_session_maker() as s:
            first_chunk = (
                await s.execute(
                    select(ChunkModel)
                    .where(ChunkModel.source_id == result.source_id)
                    .order_by(ChunkModel.chunk_index)
                    .limit(1)
                )
            ).scalar_one()
        query = first_chunk.content.strip()[:50]
        assert query, "첫 청크 content가 비어있음 — 쿼리 만들 수 없음"

        semantic = SemanticSearchClient(test_session_maker, embedder)
        keyword = KeywordSearchClient(test_session_maker)
        hybrid = HybridSearchClient(semantic, keyword)

        hits = await hybrid.search(query, project.id, top_k=10)
        assert len(hits) > 0
        hit_ids = {h.chunk_id for h in hits}
        assert first_chunk.id in hit_ids, "자기 자신 쿼리가 자기 hit에 없음"

        reranked = await rerank_hits(reranker, query, hits, top_k=5)
        assert len(reranked) <= 5
        # 리랭킹 후 score_source는 항상 reranker
        assert all(h.score_source == "reranker" for h in reranked)
        # 원본 점수는 metadata에 보존
        assert all("original_score" in h.metadata for h in reranked)
        assert all("original_score_source" in h.metadata for h in reranked)


# ===========================================================================
# 데이터 불변식
# ===========================================================================


@pytest.mark.skipif(not SAMPLE_HWPX.exists(), reason="sample.hwpx 미존재")
@pytest.mark.requires_model
class TestDataInvariants:
    """실제 DB 트랜잭션 환경에서만 잡히는 데이터 불변식."""

    async def test_reindex_with_real_data_is_idempotent(
        self,
        test_session: AsyncSession,
        test_session_maker: async_sessionmaker,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        embedder: BgeM3Client,
    ):
        """같은 파일 재인덱싱 → source_id 동일 + 청크 수 동일 + DB row 수 일치."""
        project, node = await _seed_project_and_library_node(test_session)

        first = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
        )
        second = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
        )

        assert first.source_id == second.source_id, "재인덱싱이 새 source row를 만듦"
        assert first.chunks_created == second.chunks_created

        async with test_session_maker() as s:
            row_count = (
                await s.execute(
                    select(func.count(ChunkModel.id)).where(ChunkModel.source_id == first.source_id)
                )
            ).scalar_one()
        # DELETE-INSERT 정책 — 중복 누적 없이 정확히 N개 잔존
        assert row_count == first.chunks_created

    async def test_track_filter_isolates_search_across_sources(
        self,
        test_session: AsyncSession,
        test_session_maker: async_sessionmaker,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        embedder: BgeM3Client,
    ):
        """서로 다른 source에 content / style 트랙을 각각 박고 트랙 필터로 분리 검색."""
        # 현재 source UPSERT 키가 (project_id, library_node_id)라 같은 source에 두 트랙을 동시에
        # 두려면 별도 library_node로 등록해야 한다. 본 테스트는 그 가정 위에서 트랙 필터의
        # 격리 동작을 검증한다.
        project, node_content = await _seed_project_and_library_node(
            test_session, node_name="sample.hwpx-content"
        )
        node_style = LibraryNode(name="sample.hwpx-style", type="file", file_path=str(SAMPLE_HWPX))
        test_session.add(node_style)
        await test_session.commit()
        await test_session.refresh(node_style)

        content_result = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node_content.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
            track="content",
        )
        style_result = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node_style.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
            track="style",
        )
        assert content_result.source_id != style_result.source_id

        # 같은 쿼리에 대해 트랙 필터로 결과가 분리되는지
        async with test_session_maker() as s:
            content_chunk = (
                await s.execute(
                    select(ChunkModel)
                    .where(ChunkModel.source_id == content_result.source_id)
                    .order_by(ChunkModel.chunk_index)
                    .limit(1)
                )
            ).scalar_one()
        query = content_chunk.content[:30]

        keyword = KeywordSearchClient(test_session_maker)
        content_hits = await keyword.search(query, project.id, track="content", top_k=20)
        style_hits = await keyword.search(query, project.id, track="style", top_k=20)

        async with test_session_maker() as s:
            content_chunk_ids = set(
                (
                    await s.execute(
                        select(ChunkModel.id).where(
                            ChunkModel.source_id == content_result.source_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            style_chunk_ids = set(
                (
                    await s.execute(
                        select(ChunkModel.id).where(ChunkModel.source_id == style_result.source_id)
                    )
                )
                .scalars()
                .all()
            )

        # content 트랙 검색 결과의 모든 hit은 content source 소속
        assert all(h.chunk_id in content_chunk_ids for h in content_hits), (
            "content 트랙 검색에 다른 트랙 청크가 섞임"
        )
        assert all(h.chunk_id in style_chunk_ids for h in style_hits), (
            "style 트랙 검색에 다른 트랙 청크가 섞임"
        )


# ===========================================================================
# 검색 결과 무결성
# ===========================================================================


@pytest.mark.skipif(not SAMPLE_HWPX.exists(), reason="sample.hwpx 미존재")
@pytest.mark.requires_model
class TestSearchIntegrity:
    """검색·리랭킹이 실제 인덱스 상태와 정합."""

    async def test_all_search_results_exist_in_chunks_table(
        self,
        test_session: AsyncSession,
        test_session_maker: async_sessionmaker,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        embedder: BgeM3Client,
    ):
        """HNSW + pgroonga + hybrid 결과의 모든 chunk_id가 실제 chunks 테이블에 존재."""
        project, node = await _seed_project_and_library_node(test_session)
        result = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
        )
        assert result.chunks_created > 0

        async with test_session_maker() as s:
            first_chunk = (
                await s.execute(
                    select(ChunkModel)
                    .where(ChunkModel.source_id == result.source_id)
                    .order_by(ChunkModel.chunk_index)
                    .limit(1)
                )
            ).scalar_one()
        query = first_chunk.content[:30]

        semantic = SemanticSearchClient(test_session_maker, embedder)
        keyword = KeywordSearchClient(test_session_maker)
        hybrid = HybridSearchClient(semantic, keyword)

        all_hits = (
            await hybrid.search(query, project.id, top_k=20)
            + await semantic.search(query, project.id, top_k=20)
            + await keyword.search(query, project.id, top_k=20)
        )

        async with test_session_maker() as s:
            db_chunk_ids = set(
                (await s.execute(select(ChunkModel.id).where(ChunkModel.project_id == project.id)))
                .scalars()
                .all()
            )

        orphans = [str(h.chunk_id) for h in all_hits if h.chunk_id not in db_chunk_ids]
        assert orphans == [], f"검색 결과에 DB에 없는 chunk_id 존재: {orphans}"

    async def test_rerank_preserves_upstream_score_in_metadata(
        self,
        test_session: AsyncSession,
        test_session_maker: async_sessionmaker,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        embedder: BgeM3Client,
        reranker: BgeRerankerV2M3Client,
    ):
        """실제 hybrid hit을 reranker에 넣어도 original_score / original_source 보존."""
        project, node = await _seed_project_and_library_node(test_session)
        result = await _index_sample(
            session_maker=test_session_maker,
            project_id=project.id,
            library_node_id=node.id,
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedder=embedder,
        )
        assert result.chunks_created > 0

        async with test_session_maker() as s:
            first_chunk = (
                await s.execute(
                    select(ChunkModel)
                    .where(ChunkModel.source_id == result.source_id)
                    .order_by(ChunkModel.chunk_index)
                    .limit(1)
                )
            ).scalar_one()
        query = first_chunk.content[:30]

        semantic = SemanticSearchClient(test_session_maker, embedder)
        keyword = KeywordSearchClient(test_session_maker)
        hybrid = HybridSearchClient(semantic, keyword)
        hits = await hybrid.search(query, project.id, top_k=10)
        assert len(hits) > 0
        assert all(h.score_source == "hybrid" for h in hits)

        reranked = await rerank_hits(reranker, query, hits, top_k=5)
        for h in reranked:
            assert h.score_source == "reranker"
            # 리랭킹 헬퍼의 metadata 보존 정책 — 갱신 전 score / source 둘 다 살아있다
            assert h.metadata["original_score_source"] == "hybrid"
            assert isinstance(h.metadata["original_score"], float)
            assert h.metadata["original_score"] > 0.0
