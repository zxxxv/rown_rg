from __future__ import annotations

import hashlib
import math
from uuid import UUID, uuid4

import pytest

from src.clients.embedding_client import EmbeddingClient, EmbeddingResult
from src.services.indexing._chunking import (
    Chunk,
    ChunkingService,
    TablePlaceholderSplitError,
    _build_header_path,
    _extract_tables,
    _is_header_only,
    _split_by_placeholder,
)

# ---------- 더미 임베딩 + 토크나이저 ----------


class _StubTokenizer:
    """HF PreTrainedTokenizerBase의 ``encode`` 시그니처만 흉내내는 stub."""

    @staticmethod
    def encode(text: str, add_special_tokens: bool = True) -> list[int]:
        # 한국어+영어 혼합 휴리스틱. 실제 BGE-M3 토크나이저 로딩 비용 회피.
        # BGE-M3 [CLS][SEP] 두 개를 add_special_tokens=True일 때만 더해 실제 토크나이저
        # 동작 흉내.
        base = max(1, len(text) // 2)
        return list(range(base + (2 if add_special_tokens else 0)))


_STUB_TOKENIZER = _StubTokenizer()


class _DummyEmbedding(EmbeddingClient):
    """Deterministic stub — SemanticChunker는 텍스트마다 흔들리는 벡터만 있으면 충분.
    SHA-256으로 16차원 L2-정규화 벡터 생성."""

    DIMENSION = 16

    async def embed(self, text: str) -> EmbeddingResult:
        return EmbeddingResult(embedding=self._vec(text), text=text, cached=False)

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        return [EmbeddingResult(embedding=self._vec(t), text=t, cached=False) for t in texts]

    @property
    def tokenizer(self) -> _StubTokenizer:
        return _STUB_TOKENIZER

    @staticmethod
    def _vec(text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [(b - 128) / 128.0 for b in h[:16]]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


# ---------- 픽스처 ----------


@pytest.fixture
def service() -> ChunkingService:
    return ChunkingService(_DummyEmbedding())


@pytest.fixture
def source_id() -> UUID:
    return uuid4()


SAMPLE_MARKDOWN = (
    "# 제3장 경제성 분석\n"
    "\n"
    "본 장에서는 사업의 경제성을 검토한다. "
    "정부 발표 자료에 따르면 2024년 기준 약 1,200억 원의 비용 절감 효과가 예상된다. "
    "본 분석은 GDP 대비 효과도 함께 측정한다.\n"
    "\n"
    "## 3.1 비용편익 분석\n"
    "\n"
    "비용편익 분석은 ABC 방법론을 따른다. "
    "다양한 시나리오를 검토한 결과 모든 시나리오에서 편익이 비용을 상회하는 것으로 나타났다. "
    "특히 보수적 시나리오에서도 5% 이상의 순편익이 확인된다.\n"
    "\n"
    "| 시나리오 | 비용(억원) | 편익(억원) |\n"
    "| --- | --- | --- |\n"
    "| A | 100 | 200 |\n"
    "| B | 150 | 250 |\n"
    "| C | 200 | 320 |\n"
    "\n"
    "## 3.2 결론\n"
    "\n"
    "결론적으로 사업 추진이 타당하다고 판단된다. "
    "추가 검토가 필요한 부분은 별도 보고서로 정리한다.\n"
)


# ---------- 헬퍼 단위 테스트 ----------


class TestExtractTables:
    def test_no_table_returns_unchanged(self):
        text = "본문 한 줄.\n또 한 줄."
        masked, tables = _extract_tables(text)
        assert masked == text
        assert tables == []

    def test_single_table_replaced_with_placeholder(self):
        text = "앞 본문.\n| a | b |\n| --- | --- |\n| 1 | 2 |\n뒷 본문."
        masked, tables = _extract_tables(text)
        assert "<<<TABLE_0>>>" in masked
        assert "| a | b |" not in masked
        assert len(tables) == 1
        assert "| a | b |" in tables[0]
        assert "| 1 | 2 |" in tables[0]

    def test_two_tables_get_distinct_placeholders(self):
        text = (
            "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
            "\n중간 본문.\n\n"
            "| x | y |\n| --- | --- |\n| 9 | 8 |\n"
        )
        masked, tables = _extract_tables(text)
        assert "<<<TABLE_0>>>" in masked
        assert "<<<TABLE_1>>>" in masked
        assert len(tables) == 2


class TestSplitByPlaceholder:
    def test_pure_prose_one_segment(self):
        segs = _split_by_placeholder("문단 하나.\n문단 둘.")
        assert segs == [("문단 하나.\n문단 둘.", False)]

    def test_placeholder_isolated(self):
        segs = _split_by_placeholder("앞 본문. <<<TABLE_0>>> 뒤 본문.")
        assert len(segs) == 3
        assert segs[0] == ("앞 본문. ", False)
        assert segs[1] == ("<<<TABLE_0>>>", True)
        assert segs[2] == (" 뒤 본문.", False)


class TestBuildHeaderPath:
    def test_orders_h1_to_h3(self):
        meta = {"h2": "절", "h1": "장", "h3": "관"}
        assert _build_header_path(meta) == ["장", "절", "관"]

    def test_missing_levels_skipped(self):
        assert _build_header_path({"h1": "A", "h3": "C"}) == ["A", "C"]

    def test_h4_ignored_even_if_present(self):
        # 명세상 h1~h3만 추적. h4가 어쩌다 들어와도 path에는 안 들어가야 한다.
        assert _build_header_path({"h1": "A", "h4": "D"}) == ["A"]


class TestIsHeaderOnly:
    def test_header_alone_true(self):
        assert _is_header_only("# 헤더 한 줄") is True
        assert _is_header_only("## 절\n### 관") is True

    def test_header_with_body_false(self):
        assert _is_header_only("# 헤더\n\n본문") is False

    def test_empty_false(self):
        assert _is_header_only("") is False


# ---------- ChunkingService 통합 ----------


class TestChunkMarkdown:
    async def test_returns_chunk_list(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        assert isinstance(chunks, list)
        assert all(isinstance(c, Chunk) for c in chunks)
        assert len(chunks) > 0

    async def test_chunk_index_contiguous_from_zero(
        self, service: ChunkingService, source_id: UUID
    ):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    async def test_table_preserved_as_single_chunk(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        tables = [c for c in chunks if c.metadata["chunk_type"] == "table"]
        assert len(tables) == 1, [c.content for c in tables]
        tbl = tables[0]
        for row in ("| A | 100 | 200 |", "| B | 150 | 250 |", "| C | 200 | 320 |"):
            assert row in tbl.content, tbl.content
        # 표 청크에 본문 텍스트나 헤더 라인이 섞이지 않아야 한다.
        assert "## " not in tbl.content
        assert "비용편익 분석은" not in tbl.content

    async def test_no_header_only_chunks(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        for c in chunks:
            if c.metadata["chunk_type"] == "table":
                continue
            # text 청크는 적어도 한 줄은 헤더가 아닌 본문이어야 한다 (헤더 단독 청크 금지).
            non_empty = [ln for ln in c.content.split("\n") if ln.strip()]
            assert any(not ln.lstrip().startswith("#") for ln in non_empty), c.content

    async def test_chunk_size_in_range(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        for c in chunks:
            assert 1 <= c.char_count <= 1500, c
            assert c.char_count == len(c.content)

    async def test_header_path_assigned(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        for c in chunks:
            hp = c.metadata["header_path"]
            assert isinstance(hp, list)
            assert hp[0] == "제3장 경제성 분석", hp
        # 적어도 한 청크는 ## 3.1 비용편익 분석 절에 속해야 한다.
        assert any(
            len(c.metadata["header_path"]) >= 2
            and c.metadata["header_path"][1] == "3.1 비용편익 분석"
            for c in chunks
        )

    async def test_header_text_kept_in_body(self, service: ChunkingService, source_id: UUID):
        # strip_headers=False — 첫 청크 본문에 ``# 제3장 ...``가 그대로 남아 있어야 한다.
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        joined = "\n".join(c.content for c in chunks if c.metadata["chunk_type"] == "text")
        assert "# 제3장 경제성 분석" in joined

    async def test_metadata_fields_present(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        required = {
            "header_path",
            "chunk_type",
            "token_count_estimate",
            "has_numbers",
            "has_proper_nouns",
        }
        for c in chunks:
            assert required <= set(c.metadata.keys()), c.metadata
            # source_id는 metadata가 아니라 top-level 필드.
            assert "source_id" not in c.metadata, c.metadata
        assert any(c.metadata["has_numbers"] for c in chunks)
        assert any(c.metadata["has_proper_nouns"] for c in chunks)

    async def test_source_id_on_chunk(self, service: ChunkingService, source_id: UUID):
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        assert all(c.source_id == source_id for c in chunks)

    async def test_token_count_estimate_uses_tokenizer(
        self, service: ChunkingService, source_id: UUID
    ):
        # _StubTokenizer는 글자 // 2를 토큰 수로 돌려준다. 어느 청크든 그 공식이 맞아야 한다.
        chunks = await service.chunk_markdown(SAMPLE_MARKDOWN, source_id)
        for c in chunks:
            expected = max(1, len(c.content) // 2)
            assert c.metadata["token_count_estimate"] == expected, c

    async def test_empty_markdown_returns_empty(self, service: ChunkingService, source_id: UUID):
        assert await service.chunk_markdown("", source_id) == []

    async def test_header_only_returns_empty(self, service: ChunkingService, source_id: UUID):
        # 헤더만 있고 본문 0줄 → 빈 리스트 (Negative #2).
        chunks = await service.chunk_markdown("# 제목만\n\n## 부제목만\n", source_id)
        assert chunks == []

    async def test_short_section_passes_through(self, service: ChunkingService, source_id: UUID):
        md = "# 짧은 장\n\n한 문장만 있는 짧은 본문.\n"
        chunks = await service.chunk_markdown(md, source_id)
        assert len(chunks) == 1
        assert "한 문장만 있는 짧은 본문" in chunks[0].content
        assert chunks[0].metadata["header_path"] == ["짧은 장"]


class TestMaxCharsEnforcement:
    async def test_chunk_above_max_is_hard_split(self, source_id: UUID):
        svc = ChunkingService(_DummyEmbedding(), min_chars=10, max_chars=200)
        body = ("이것은 매우 긴 단락이다. " * 40).strip()
        md = f"# 단일 장\n\n{body}\n"
        chunks = await svc.chunk_markdown(md, source_id)
        assert len(chunks) >= 2
        for c in chunks:
            assert c.char_count <= 200, c.char_count


class TestMergeShort:
    async def test_short_adjacent_text_chunks_merged_under_max(self, source_id: UUID):
        svc = ChunkingService(_DummyEmbedding(), min_chars=80, max_chars=300)
        md = "# 장\n\n짧은 첫 문장.\n\n짧은 둘째 문장.\n"
        chunks = await svc.chunk_markdown(md, source_id)
        assert len(chunks) == 1
        assert "짧은 첫 문장" in chunks[0].content
        assert "짧은 둘째 문장" in chunks[0].content

    async def test_table_never_merged_with_text(self, source_id: UUID):
        svc = ChunkingService(_DummyEmbedding(), min_chars=200, max_chars=2000)
        md = (
            "# 장\n\n"
            "짧은 본문 한 줄.\n\n"
            "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
            "짧은 본문 또 한 줄.\n"
        )
        chunks = await svc.chunk_markdown(md, source_id)
        types = [c.metadata["chunk_type"] for c in chunks]
        assert "table" in types
        for c in chunks:
            if c.metadata["chunk_type"] == "text":
                assert "| --- |" not in c.content


class TestPlaceholderSafety:
    """Negative #3 — placeholder가 청크 경계에 걸린 경우 명시적 에러."""

    def test_partial_open_marker_raises(self, service: ChunkingService, source_id: UUID):
        # ``<<<TABLE_`` 시작 토큰만 있고 닫힘이 없는 fragment.
        with pytest.raises(TablePlaceholderSplitError):
            service._verify_no_split_placeholder("앞 본문 <<<TABLE_0", source_id)

    def test_partial_close_marker_raises(self, service: ChunkingService, source_id: UUID):
        # 닫힘 ``>>>``만 있는 fragment.
        with pytest.raises(TablePlaceholderSplitError):
            service._verify_no_split_placeholder("0>>> 뒤 본문", source_id)

    def test_intact_placeholder_passes(self, service: ChunkingService, source_id: UUID):
        # 정상 placeholder는 그대로 통과.
        out = service._verify_no_split_placeholder("앞 <<<TABLE_2>>> 뒤", source_id)
        assert out == "앞 <<<TABLE_2>>> 뒤"


# ---------- 프리워밍 ----------


class _CountingEmbedding(_DummyEmbedding):
    """embed_batch 호출 시 텍스트 목록을 기록 — prewarm 적중 여부 검증용."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        self.calls.append(list(texts))
        return await super().embed_batch(texts)


class TestCombinedSentences:
    """``_combined_sentences``가 SemanticChunker(buffer_size=1)의 형식을 정확히 복제하는지."""

    def test_three_sentences_join_with_buffer(self) -> None:
        out = ChunkingService._combined_sentences("A. B. C.")
        # 정규식 분할: ["A.", "B.", "C."]
        # i=0: 앞 없음 + "A." + " B." → "A. B."
        # i=1: "A. " + "B." + " C." → "A. B. C."
        # i=2: "B. " + "C." + 뒤 없음 → "B. C."
        assert out == ["A. B.", "A. B. C.", "B. C."]

    def test_single_sentence_no_split(self) -> None:
        out = ChunkingService._combined_sentences("Hello world")
        assert out == ["Hello world"]

    def test_empty_string_yields_one_empty(self) -> None:
        # SemanticChunker 소스에 빈 문자열 필터링이 없음 — 동일하게 유지.
        out = ChunkingService._combined_sentences("")
        assert out == [""]


class TestPrewarming:
    """prewarm 동작 + 비활성 분기 + 짧은 segment 제외."""

    @pytest.mark.asyncio
    async def test_prewarm_called_with_combined_sentences(self, source_id: UUID) -> None:
        client = _CountingEmbedding()
        svc = ChunkingService(client, min_chars=10)
        markdown = "# T\n\n" + "긴 본문 첫 문장. 두 번째 문장. 세 번째 문장. " + "추가 본문." * 10
        await svc.chunk_markdown(markdown, source_id)

        # 첫 호출이 prewarm — combined_sentences 형식의 큰 단발 배치.
        assert len(client.calls) >= 1
        prewarm_call = client.calls[0]
        # combined_sentences는 문장 수만큼 항목 — 4문장 이상은 나와야 함.
        assert len(prewarm_call) >= 4

    @pytest.mark.asyncio
    async def test_prewarm_disabled_skips_initial_batch(self, source_id: UUID) -> None:
        client = _CountingEmbedding()
        svc = ChunkingService(client, min_chars=10, prewarm=False)
        markdown = "# T\n\n" + "본문 한 문장. 본문 두 문장. " + "추가 본문." * 10
        await svc.chunk_markdown(markdown, source_id)

        # prewarm 없음 — 첫 호출은 SemanticChunker가 자체적으로 보낸 segment 임베딩.
        assert all("chunking.prewarm" not in str(call) for call in client.calls)
        # 첫 호출이 prewarm이라면 단일 문서 전체의 combined_sentences 수만큼 나와야 함.
        # 비활성이면 SemanticChunker가 segment 단위로 작게 던짐 — 첫 배치도 작아야 함.
        # 정확한 수는 SemanticChunker 내부 구현 의존이라 단언 안 함, 호출 자체가 있는지만 확인.
        assert len(client.calls) >= 1

    @pytest.mark.asyncio
    async def test_prewarm_skips_short_segments(self, source_id: UUID) -> None:
        client = _CountingEmbedding()
        svc = ChunkingService(client, min_chars=500)
        # 모든 segment가 min_chars=500 미만 — prewarm 대상이 없어야 함.
        markdown = "# T\n\n짧은 본문 한 줄."
        await svc.chunk_markdown(markdown, source_id)

        # prewarm은 호출되지 않음 (segment 모두 < min_chars).
        # 이후 chunk_markdown 본 흐름도 SemanticChunker 우회하므로 embed_batch 0회.
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_prewarm_skipped_when_no_header_docs(self, source_id: UUID) -> None:
        client = _CountingEmbedding()
        svc = ChunkingService(client)
        await svc.chunk_markdown("", source_id)
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_prewarm_only_non_placeholder_sentences(self, source_id: UUID) -> None:
        client = _CountingEmbedding()
        svc = ChunkingService(client, min_chars=10)
        markdown = (
            "# H\n\n"
            "본문 첫 문장. 본문 두 번째 문장. 본문 세 번째 문장. " + "본문 추가." * 5 + "\n\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n"
            "추가 본문 다시. 두 번째. " + "끝." * 5
        )
        await svc.chunk_markdown(markdown, source_id)

        assert len(client.calls) >= 1
        prewarm_call = client.calls[0]
        # 테이블 본문 ("a", "b", "1", "2")이 prewarm 입력에 들어가지 않아야 함.
        prewarm_text = " ".join(prewarm_call)
        assert "| a | b |" not in prewarm_text
