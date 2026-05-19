from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import pytest

from src.clients.embedding_client import (
    BgeM3Client,
    EmbeddingCache,
    EmbeddingClient,
    EmbeddingResult,
)

# ---------- EmbeddingResult ----------


class TestEmbeddingResult:
    def test_roundtrip_json(self):
        r = EmbeddingResult(embedding=[0.1, 0.2, 0.3], text="안녕", cached=True)
        roundtrip = EmbeddingResult.model_validate_json(r.model_dump_json())
        assert roundtrip.embedding == r.embedding
        assert roundtrip.text == r.text
        assert roundtrip.cached is True


# ---------- EmbeddingCache ----------


class TestEmbeddingCache:
    def test_key_is_deterministic(self):
        assert EmbeddingCache._key("동일 텍스트") == EmbeddingCache._key("동일 텍스트")

    @pytest.mark.parametrize(
        "left,right",
        [
            ("안녕하세요", "안녕하세요!"),
            ("a", "A"),
            ("문장 1", "문장 2"),
            ("", " "),
        ],
    )
    def test_key_differs_for_different_text(self, left: str, right: str):
        assert EmbeddingCache._key(left) != EmbeddingCache._key(right)

    def test_path_shards_into_two_char_prefix(self, tmp_path: Path):
        cache = EmbeddingCache(root=tmp_path)
        key = EmbeddingCache._key("샘플")
        path = cache._path_for(key)
        # 정책: 첫 두 글자가 prefix 디렉토리
        assert path.parent.name == key[:2]
        assert path.name == f"{key}.npy"
        assert path.parent.parent == tmp_path

    def test_load_missing_returns_none(self, tmp_path: Path):
        cache = EmbeddingCache(root=tmp_path)
        assert cache.load("never stored") is None

    def test_load_corrupted_returns_none(self, tmp_path: Path):
        cache = EmbeddingCache(root=tmp_path)
        path = cache._path_for(cache._key("corrupt"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a numpy file")
        assert cache.load("corrupt") is None

    def test_store_then_load_roundtrip(self, tmp_path: Path):
        cache = EmbeddingCache(root=tmp_path)
        vec = np.array([0.1, -0.2, 0.3, 0.4], dtype=np.float32)
        cache.store("hello", vec)
        loaded = cache.load("hello")
        assert loaded is not None
        assert loaded.dtype == np.float32
        assert np.allclose(loaded, vec)

    def test_store_creates_missing_directory(self, tmp_path: Path):
        cache = EmbeddingCache(root=tmp_path / "nested" / "deeper")
        cache.store("새 텍스트", np.zeros(4, dtype=np.float32))
        assert cache.load("새 텍스트") is not None


# ---------- ABC contract ----------


class TestEmbeddingClientABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            EmbeddingClient()  # type: ignore[abstract]

    def test_bge_m3_subclasses_embedding_client(self):
        assert issubclass(BgeM3Client, EmbeddingClient)

    def test_dimension_is_classvar(self):
        assert BgeM3Client.DIMENSION == 1024


# ---------- 동적 배치 (model-free) ----------


class TestDynamicBatching:
    """``_make_dynamic_batches``는 staticmethod라 모델 없이 직접 호출 가능."""

    @pytest.mark.parametrize(
        "texts,max_chars,expected_n_batches,expected_first_len,expected_last_len",
        [
            # 짧은 단락 32개 (총 3200자) → 한도 10000자 안에 묶임
            ([("x" * 100)] * 32, 10_000, 1, 32, 32),
            # 짧은 단락 100개 (총 10000자, 정확히 한도) → 묶임
            ([("x" * 100)] * 100, 10_000, 1, 100, 100),
            # 5000자 단락 32개 → 2개씩 묶여 16배치 (5000+5000=10000, +5000=15000 splits)
            ([("x" * 5000)] * 32, 10_000, 16, 2, 2),
            # 빈 입력
            ([], 10_000, 0, 0, 0),
            # 단일 단락이 한도 초과 → 예외 안 던지고 단독 배치 (tokenizer truncation에 위임)
            ([("x" * 50_000)], 10_000, 1, 1, 1),
            # 한도 정확히 일치 → 같은 배치 안에 둠 (>가 아니라 strict greater)
            ([("x" * 5000), ("x" * 5000)], 10_000, 1, 2, 2),
            # 한도 1자 초과 → 두 배치로 분할
            ([("x" * 5000), ("x" * 5001)], 10_000, 2, 1, 1),
        ],
    )
    def test_batch_grouping(
        self,
        texts: list[str],
        max_chars: int,
        expected_n_batches: int,
        expected_first_len: int,
        expected_last_len: int,
    ):
        batches = BgeM3Client._make_dynamic_batches(texts, max_chars)
        assert len(batches) == expected_n_batches
        if expected_n_batches > 0:
            assert len(batches[0]) == expected_first_len
            assert len(batches[-1]) == expected_last_len
            # 분할 invariant: 마지막을 제외한 모든 배치의 누적 글자 수는 한도 이하
            for batch in batches[:-1]:
                assert sum(len(t) for t in batch) <= max_chars

    def test_returns_every_input_exactly_once(self):
        """입력 누락·중복 없이 모든 텍스트가 정확히 한 번 등장."""
        texts = [f"text-{i}-{'x' * (i * 100)}" for i in range(20)]
        batches = BgeM3Client._make_dynamic_batches(texts, 2000)
        flat = [t for batch in batches for t in batch]
        assert flat == texts  # 순서까지 보존


# ---------- MAX_INPUT_CHARS 사전 truncation (model-free) ----------


class TestMaxInputCharsTruncation:
    """``MAX_INPUT_CHARS``가 embed_batch 진입 시점에 입력을 사전 차단하는지 검증.

    실모델 없이 ``_encode``를 spy로 갈아끼워 tokenizer가 받는 텍스트 길이를 확인한다.
    """

    @staticmethod
    def _make_client(tmp_path: Path) -> BgeM3Client:
        """Tokenizer/ONNX 로드를 우회하고 ``_encode``만 spy로 갈아끼운 클라이언트."""
        # __init__이 onnxruntime/transformers를 끌어오므로 우회 — staticmethod로
        # 인스턴스를 직접 조립한다.
        client = BgeM3Client.__new__(BgeM3Client)
        client._cache = EmbeddingCache(root=tmp_path)
        client._max_chars_per_batch = 100_000
        client._max_length = 512
        return client

    def test_max_input_chars_classvar_present(self):
        assert isinstance(BgeM3Client.MAX_INPUT_CHARS, int)
        # 512 토큰 × 한국어 ~4자/토큰 worst case = ~2000자. 그 위로 안전 margin 확보.
        assert BgeM3Client.MAX_INPUT_CHARS >= 2_000

    def test_encode_receives_truncated_text(self, tmp_path: Path):
        client = self._make_client(tmp_path)
        cap = BgeM3Client.MAX_INPUT_CHARS

        seen: list[str] = []

        def spy_encode(texts: list[str]) -> np.ndarray:
            seen.extend(texts)
            return np.zeros((len(texts), BgeM3Client.DIMENSION), dtype=np.float32)

        client._encode = spy_encode  # type: ignore[method-assign]

        huge = "긴" * (cap * 3)  # 명백히 cap 초과
        asyncio.run(client.embed_batch([huge]))

        assert len(seen) == 1
        assert len(seen[0]) == cap, f"_encode가 cut 안 된 길이를 받음: {len(seen[0])} != {cap}"

    def test_result_text_preserves_original(self, tmp_path: Path):
        client = self._make_client(tmp_path)
        cap = BgeM3Client.MAX_INPUT_CHARS

        def stub_encode(texts: list[str]) -> np.ndarray:
            return np.zeros((len(texts), BgeM3Client.DIMENSION), dtype=np.float32)

        client._encode = stub_encode  # type: ignore[method-assign]

        original = "원본" * (cap * 2)
        results = asyncio.run(client.embed_batch([original]))

        # API 계약: 호출자가 준 원본을 그대로 돌려준다 (routing 용도)
        assert results[0].text == original
        assert results[0].text != original[:cap]

    def test_cache_key_is_truncated_text(self, tmp_path: Path):
        """cut된 prefix가 같으면 캐시가 적중해야 한다 — 동일 임베딩이므로 의도된 동작."""
        client = self._make_client(tmp_path)
        cap = BgeM3Client.MAX_INPUT_CHARS

        calls = 0

        def counting_encode(texts: list[str]) -> np.ndarray:
            nonlocal calls
            calls += 1
            # 결정적 dummy 벡터 — 캐시된 값과 비교 가능하게 normalize
            arr = np.zeros((len(texts), BgeM3Client.DIMENSION), dtype=np.float32)
            arr[:, 0] = 1.0
            return arr

        client._encode = counting_encode  # type: ignore[method-assign]

        prefix = "공유 prefix" * (cap // 5)  # cap 이상 보장
        a = prefix + "_꼬리A"
        b = prefix + "_꼬리B"

        asyncio.run(client.embed_batch([a]))
        # b는 a와 cut된 prefix가 동일 → 캐시 적중, _encode 호출 추가 없음
        result = asyncio.run(client.embed_batch([b]))

        assert calls == 1, f"prefix 동일 입력에 _encode가 재호출됨: calls={calls}"
        assert result[0].cached is True
        assert result[0].text == b  # API 계약: 호출자 원본 보존

    def test_short_input_unchanged(self, tmp_path: Path):
        """cap 이하 입력은 truncation도 cache key 변환도 없어야 함."""
        client = self._make_client(tmp_path)

        seen: list[str] = []

        def spy_encode(texts: list[str]) -> np.ndarray:
            seen.extend(texts)
            return np.zeros((len(texts), BgeM3Client.DIMENSION), dtype=np.float32)

        client._encode = spy_encode  # type: ignore[method-assign]

        short = "짧은 단락"
        asyncio.run(client.embed_batch([short]))
        assert seen == [short]


# ---------- 안전 가드 ClassVar ----------


class TestSafetyGuardClassVars:
    def test_memory_pressure_threshold_lowered(self):
        # 16GB + swap 4GB 환경에서 80%는 이미 thrash 직전이라 더 일찍 발동해야 한다.
        assert BgeM3Client.MEMORY_PRESSURE_THRESHOLD <= 70

    def test_override_multiplier_present_and_finite(self):
        m = BgeM3Client.MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER
        assert isinstance(m, int | float)
        # 자동값 위는 chunk-사이 watchdog의 사각지대로 가는 길이라 multiplier는 1.0 이하.
        assert 0 < m <= 1.0


class TestAutoMaxCharsStaticmethod:
    """``auto_max_chars_per_batch``는 외부에서 호스트 자동값을 참조하기 위한 진입점."""

    def test_returns_positive_int(self):
        v = BgeM3Client.auto_max_chars_per_batch()
        assert isinstance(v, int)
        assert v > 0


class TestMaxCharsOverrideHardCap:
    """``max_chars_per_batch`` override가 호스트 RAM 자동값의 multiplier 배를 넘으면 raise."""

    def test_override_exceeding_hard_cap_raises(self):
        auto = BgeM3Client.auto_max_chars_per_batch()
        m = BgeM3Client.MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER
        # cap 위 1자 — 모델 로드 이전 단계에서 raise 발생을 검증하기 위해 안전 범위 밖 값.
        too_big = int(auto * m) + 1
        with pytest.raises(ValueError, match="exceeds hard cap"):
            BgeM3Client(max_chars_per_batch=too_big)


# ---------- BgeM3Client (실제 모델) ----------

# 모델 셋업이 끝났을 때만 통합 단위 테스트 실행. CI에서 모델 없을 때 자동 skip.
MODEL_ROOT = Path("./models/bge-m3-onnx-int8")
SKIP_REASON = "BGE-M3 ONNX 모델이 없음 — scripts/setup_bge_m3.py 먼저 실행"


def _model_available() -> bool:
    return (MODEL_ROOT / "model.onnx").exists() and (MODEL_ROOT / "tokenizer.json").exists()


@pytest.fixture(scope="module")
def bge_client(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if not _model_available():
        pytest.skip(SKIP_REASON)
    os.environ.setdefault("HF_HOME", "./cache/huggingface")
    cache_root = tmp_path_factory.mktemp("emb_cache")
    return BgeM3Client(cache=EmbeddingCache(root=cache_root))


@pytest.mark.skipif(not _model_available(), reason=SKIP_REASON)
class TestBgeM3Client:
    def test_embed_returns_1024_dim(self, bge_client: BgeM3Client):
        result = asyncio.run(bge_client.embed("안녕하세요"))
        assert isinstance(result, EmbeddingResult)
        assert len(result.embedding) == 1024
        assert result.text == "안녕하세요"

    def test_embedding_is_l2_normalized(self, bge_client: BgeM3Client):
        result = asyncio.run(bge_client.embed("L2 정규화 확인 문장"))
        norm = float(np.linalg.norm(result.embedding))
        assert norm == pytest.approx(1.0, abs=0.01)

    def test_first_call_not_cached_second_is(self, tmp_path: Path):
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path))
        first = asyncio.run(client.embed("캐시 검증 문장"))
        assert first.cached is False
        second = asyncio.run(client.embed("캐시 검증 문장"))
        assert second.cached is True
        # 캐시된 벡터는 직전 계산과 동일해야 함 (캐시 corruption 감지)
        assert np.allclose(first.embedding, second.embedding, atol=1e-5)

    def test_embed_batch_preserves_order_and_count(self, bge_client: BgeM3Client):
        texts = [
            "2023년 노인실태조사 결과 발표",
            "예비타당성조사 제도 개정 동향",
            "기획재정부의 사업 평가 기준",
        ]
        results = asyncio.run(bge_client.embed_batch(texts))
        assert len(results) == len(texts)
        for r, t in zip(results, texts, strict=True):
            assert r.text == t
            assert len(r.embedding) == 1024

    def test_embed_batch_mixes_cache_and_fresh(self, tmp_path: Path):
        """미적중·적중이 한 배치에 섞여도 순서/플래그가 맞아야 함."""
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path))
        # 첫 호출로 a, b를 캐시에 적재
        first = asyncio.run(client.embed_batch(["a 문장", "b 문장"]))
        assert all(r.cached is False for r in first)

        # 두 번째 호출: a는 캐시, c는 신규, b는 캐시 — 순서 보존 + flag 정확
        second = asyncio.run(client.embed_batch(["a 문장", "c 문장", "b 문장"]))
        assert [r.text for r in second] == ["a 문장", "c 문장", "b 문장"]
        assert [r.cached for r in second] == [True, False, True]

    def test_empty_batch_returns_empty(self, bge_client: BgeM3Client):
        assert asyncio.run(bge_client.embed_batch([])) == []

    def test_max_chars_override_drives_forward_count(self, tmp_path: Path):
        """max_chars_per_batch가 forward pass 분기를 실제로 만든다.

        INT8 모델은 padding-quantization 상호작용으로 batch 크기에 따라
        결과값이 미세하게 흔들려 값 비교로는 검증할 수 없다 (실측 cos≈0.98).
        대신 ``_encode`` 호출 횟수로 분기를 검증한다.
        """
        client = BgeM3Client(
            cache=EmbeddingCache(root=tmp_path / "spy"),
            max_chars_per_batch=12,
        )
        assert client._max_chars_per_batch == 12

        call_count = 0
        original_encode = client._encode

        def counting_encode(texts: list[str]) -> np.ndarray:
            nonlocal call_count
            call_count += 1
            return original_encode(texts)

        client._encode = counting_encode  # type: ignore[method-assign]
        # 각 텍스트 5자, max_chars=12 + sort_for_padding=False:
        # [t1,t2]=10 → [t3]+t4=10 → [t5]=5 → 정확히 3배치 (10+5=15>12 splits)
        texts = ["문장 하나", "문장 둘1", "문장 셋2", "문장 넷3", "문장 다섯"]
        asyncio.run(client.embed_batch(texts, sort_for_padding=False))
        assert call_count == 3, f"max_chars=12에서 expected=3 batches, got {call_count}"

    def test_sort_for_padding_orders_encode_input_by_length(self, tmp_path: Path):
        """기본(sort_for_padding=True)이면 _encode가 글자 수 오름차순 텍스트를 받는다."""
        # 입력 총 81자라 자동값(≥16k) 한도 안에서 한 배치로 묶임. override 불필요.
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path / "sort_on"))
        seen: list[str] = []
        original_encode = client._encode

        def spy_encode(texts: list[str]) -> np.ndarray:
            seen.extend(texts)
            return original_encode(texts)

        client._encode = spy_encode  # type: ignore[method-assign]
        inputs = ["b" * 50, "a", "c" * 30]  # 길이 50, 1, 30
        asyncio.run(client.embed_batch(inputs))
        assert seen == [
            "a",
            "c" * 30,
            "b" * 50,
        ], f"sort_for_padding=True에서 _encode가 길이순으로 받아야 함: {[len(s) for s in seen]}"

    def test_sort_for_padding_disabled_preserves_encode_order(self, tmp_path: Path):
        """sort_for_padding=False면 _encode가 원래 순서로 받는다."""
        # 입력 총 81자라 자동값 안에서 한 배치로 묶임. override 불필요.
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path / "sort_off"))
        seen: list[str] = []
        original_encode = client._encode

        def spy_encode(texts: list[str]) -> np.ndarray:
            seen.extend(texts)
            return original_encode(texts)

        client._encode = spy_encode  # type: ignore[method-assign]
        inputs = ["b" * 50, "a", "c" * 30]
        asyncio.run(client.embed_batch(inputs, sort_for_padding=False))
        assert seen == inputs

    def test_sort_for_padding_returns_in_original_order(self, tmp_path: Path):
        """내부 정렬이 있어도 결과는 입력 순서로 매핑된다.

        동일 텍스트는 동일 임베딩이 나온다는 결정성을 이용해 검증 — 입력 슬롯 0과 2에
        같은 텍스트를 두고, 정렬되었어도 결과 슬롯 0/2의 embedding이 동일한지 확인.
        """
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path / "order"))
        # 짧은-긴-짧은 패턴: sort_for_padding=True면 내부 순서 [a, a, b*1000]로 재배열되지만
        # 반환은 입력 순서. 슬롯 0과 2가 같은 텍스트라 임베딩도 동일해야 한다.
        texts = ["a", "b" * 1000, "a"]
        result = asyncio.run(client.embed_batch(texts))
        assert [r.text for r in result] == texts
        np.testing.assert_allclose(result[0].embedding, result[2].embedding, rtol=0, atol=1e-6)
        # 그리고 두 번째 슬롯은 다른 텍스트 → 임베딩도 달라야 함
        assert not np.allclose(result[0].embedding, result[1].embedding, atol=1e-3)

    def test_huge_single_paragraph_no_oom(self, tmp_path: Path):
        """14000자 단일 단락이 embed_batch 진입 시점에 cut되어 OOM 없이 통과.

        ``MAX_INPUT_CHARS`` cap이 tokenizer 단계 RAM 폭주를 차단하는지 검증 —
        14000자급 단락이 fast tokenizer 중간 구조를 메모리에 통째로 올려 OOM을
        유발하는 시나리오 대응.
        """
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path / "huge_single"))

        proc = psutil.Process()
        before_rss = proc.memory_info().rss
        huge_text = "노인실태조사 데이터 " * 1500  # ~ 15000자
        assert len(huge_text) > BgeM3Client.MAX_INPUT_CHARS, "테스트 전제: cap 초과 길이"

        result = asyncio.run(client.embed_batch([huge_text]))
        peak_rss = proc.memory_info().rss

        assert len(result) == 1
        assert len(result[0].embedding) == 1024
        assert result[0].text == huge_text  # 원본 보존
        delta_gb = (peak_rss - before_rss) / 1024**3
        assert delta_gb < 2, f"단일 거대 단락에서 메모리 증가 {delta_gb:.2f}GB"

    def test_truncation_is_semantic_noop(self, tmp_path: Path):
        """cap 초과 입력의 임베딩이 cut된 prefix만 입력한 결과와 동일해야 한다.

        Prefix-stable tokenization + max_length=512 truncation 조합의 결과로,
        모델이 보는 첫 512토큰이 동일하므로 임베딩도 동일해야 한다. 만약 cap이
        너무 작아 512토큰 안에 다 들어가지 못하면 이 가정이 깨진다.
        """
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path / "noop"))

        cap = BgeM3Client.MAX_INPUT_CHARS
        # cap 길이가 이미 512토큰 한참 넘는지 확인 — 한국어 보고서 텍스트로 검증
        base = "기획재정부 예비타당성조사 운용지침에 따라 사업의 타당성을 검토한다. "
        # cap 의 3배 길이 확보
        long_text = (base * (cap * 3 // len(base) + 1))[: cap * 3]
        assert len(long_text) > cap

        long_result = asyncio.run(client.embed_batch([long_text]))
        prefix_only = long_text[:cap]
        prefix_result = asyncio.run(client.embed_batch([prefix_only]))

        np.testing.assert_allclose(
            long_result[0].embedding,
            prefix_result[0].embedding,
            rtol=0,
            atol=1e-6,
            err_msg="cap 초과 입력과 cut prefix의 임베딩이 다름 — prefix-stability 위반",
        )

    def test_huge_paragraphs_no_oom(self, tmp_path: Path):
        """14000자급 단락 32개 처리 시 메모리 증가가 4GB 미만.

        같은 부하 패턴이 시스템 OOM을 유발한 적이 있어 회귀 테스트로 박음.
        동적 배치(누적 글자 수 상한)와 padding 정렬이 메모리를 균일화하는지 검증.
        """
        client = BgeM3Client(cache=EmbeddingCache(root=tmp_path / "oom"))

        proc = psutil.Process()
        before_rss = proc.memory_info().rss
        # 동일 텍스트 32개. 내부 캐시 적중을 막기 위해 인덱스로 변형.
        texts = [f"{i:03d}-" + ("긴 단락 " * 1000) for i in range(32)]
        # 각 텍스트 ~6004자, 총 ~192k자. max_chars=32k 가정 시 약 5~6배치로 분할 예상.
        result = asyncio.run(client.embed_batch(texts))
        peak_rss = proc.memory_info().rss

        assert len(result) == 32
        assert all(len(r.embedding) == 1024 for r in result)
        delta_gb = (peak_rss - before_rss) / 1024**3
        assert delta_gb < 4, f"메모리 증가 {delta_gb:.2f}GB가 4GB 임계 초과"
