"""Text embedding clients.

Provides an abstract :class:`EmbeddingClient` interface, a disk-backed cache,
and a concrete :class:`BgeM3Client` adapter backed by an ONNX INT8 model.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import numpy as np
import psutil
import sentry_sdk
import structlog
from pydantic import BaseModel

from src.clients.onnx_text_embedder import OnnxTextEmbedder, chars_budget_for_bytes
from src.core.config import settings

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

logger = structlog.get_logger(__name__)

# 메모리 압박을 Sentry에 이미 올렸는가. 압박은 한 색인 잡 안에서 배치마다 반복되고
# 그때마다 이벤트를 올리면 한 번의 색인으로 수십 건이 쌓인다. 신호는 "이 프로세스가
# 임계를 넘었다" 한 번이면 충분하고, 배치별 상세는 logger.warning에 그대로 남는다.
_MEMORY_PRESSURE_REPORTED = False


class EmbeddingResult(BaseModel):
    """One embedded text plus its provenance.

    Attributes:
        embedding: Dense vector, length equal to the client's ``DIMENSION``.
        text: Original input text, preserved so callers can route results
            without separately tracking input order.
        cached: True if the embedding was loaded from the disk cache,
            False if it was just computed.
    """

    embedding: list[float]
    text: str
    cached: bool


class EmbeddingClient(ABC):
    """Abstract embedding client.

    Subclasses must declare ``DIMENSION`` (output vector size) and implement
    both :meth:`embed` and :meth:`embed_batch`. ``embed_batch`` exists as a
    separate abstract method (not a default fan-out over :meth:`embed`) so
    each backend can group cache misses into a single forward pass.
    """

    DIMENSION: ClassVar[int]

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """Embed a single text."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed multiple texts, preserving input order."""


class EmbeddingCache:
    """Disk cache for embeddings, keyed by SHA-256 of (모델 지문 + 입력 텍스트).

    Files are sharded into 256 subdirectories using the first two hex chars
    of the key, so a single directory does not accumulate millions of files
    (which would slow down ext4 listings and inflate inode usage).

    **지문이 키에 들어가는 이유.** 벡터는 텍스트만으로 결정되지 않는다 - 어느 모델을
    어느 dtype으로 돌렸는지가 같이 결정한다. 지문 없이 텍스트만 해싱하면, 모델을
    바꾸고 전량 재색인을 돌려도 **캐시에 있는 텍스트는 옛 벡터가 그대로 나온다**.
    새 모델을 부르지도 않는다. 그러면 색인이 두 공간에 걸쳐 섞이고, 에러는 하나도
    안 난다. 운영 캐시에 15,465건이 쌓여 있던 상태라 실제로 밟을 뻔했다(2026-08-20).

    지문이 바뀌면 옛 항목은 그냥 매칭되지 않는다 - 지울 필요가 없고, 되돌리면 다시
    쓰인다. 디스크만 차지하므로 한가할 때 청소하면 된다.
    """

    def __init__(self, root: Path | str = "./cache/embeddings", *, fingerprint: str = "") -> None:
        self.root = Path(root)
        self._fingerprint = fingerprint

    def _key(self, text: str) -> str:
        # 길이 접두사로 지문과 본문을 구분한다 - 단순 이어붙이기는 다른 (지문, 텍스트)
        # 쌍이 같은 문자열로 접히는 이론적 충돌이 있다.
        h = hashlib.sha256()
        fp = self._fingerprint.encode("utf-8")
        h.update(len(fp).to_bytes(4, "big"))
        h.update(fp)
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    def _path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.npy"

    def load(self, text: str) -> np.ndarray | None:
        """Return the cached embedding for ``text``, or None on miss/error."""
        path = self._path_for(self._key(text))
        if not path.exists():
            return None
        try:
            return np.load(path)
        except Exception as e:
            # 손상된 캐시 파일을 만나도 호출자는 정상 재계산으로 이어져야 하므로 None 반환.
            logger.warning(
                "embedding.cache.load_failed",
                path=str(path),
                error_type=type(e).__name__,
            )
            return None

    def store(self, text: str, embedding: np.ndarray) -> None:
        """Persist ``embedding`` for ``text`` as float32 .npy."""
        path = self._path_for(self._key(text))
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, embedding.astype(np.float32))


class BgeM3Client(EmbeddingClient):
    """BGE-M3 ONNX INT8 embedding client.

    Uses CLS pooling on ``token_embeddings`` plus L2 normalization, matching
    the configuration validated in ``reports/bge_m3_setup.md``
    (INT8 vs PyTorch mean cosine = 0.9804 on 10 Korean samples).
    The ONNX graph also exposes a fused ``sentence_embedding`` output, but
    we read ``token_embeddings`` and pool ourselves to keep the pipeline
    identical to the setup script — switching the pooling source must be
    re-validated against the report's accuracy threshold before deployment.
    """

    # BGE-M3 출력 차원, 변경 불가 (모델 가중치가 1024-d로 학습됨)
    DIMENSION: ClassVar[int] = 1024
    # BGE-M3는 8192까지 가능하나 메모리·지연 절감 위해 512 기본. 긴 문서는 분할 후 임베딩.
    MAX_LENGTH: ClassVar[int] = 512
    # mean pooling 사용 시 한국어 정확도 약 -1.5% (사전 실험 결과). 변경 시 재검증 필수.
    POOLING: ClassVar[str] = "cls"
    # 16GB RAM 기준 한 forward pass 누적 글자 수 상한. 14000자급 단락 32개를 한 배치에
    # 묶으면 ONNX 활성화 텐서가 RAM을 폭주시켜 OOM이 난다. 동적 배치는 글자 수 합이 이
    # 한도를 넘기 전에 배치를 분할해 메모리 부담을 균일화한다.
    MAX_CHARS_PER_BATCH: ClassVar[int] = 32_000
    # 단일 입력 텍스트의 글자 수 hard cap. tokenizer는 max_length=512 토큰에서 자르지만
    # 그 이전에 fast tokenizer가 빌드하는 BPE/SP 중간 구조가 통째 입력을 RAM에 올려
    # 14000자급 단락에서 OOM이 발생한다. 한국어 기준 512토큰은 ~2000자에 못 미치므로
    # 4000자 cap은 안전 margin 2배 — prefix-stable tokenization 특성상 결과 임베딩은
    # 원본과 동일하다 (모델이 보는 첫 512토큰이 같음).
    MAX_INPUT_CHARS: ClassVar[int] = 4_000
    # 가용 메모리 사용률이 이 % 초과 시 배치 사이에 강제 GC. ONNX runtime이 직전 텐서를
    # 늦게 풀 때 누적 OOM을 막는 안전망. swap 4GB 환경에서 80%는 이미 thrash 직전이라
    # 더 일찍 발동.
    MEMORY_PRESSURE_THRESHOLD: ClassVar[int] = 70
    # max_chars_per_batch override의 안전 상한 배수. 자동값을 초과하는 override는 거절 —
    # 자동값과 hard cap 사이를 통과시키면 한 forward에서 폭주하는 batch가 chunk-사이
    # watchdog보다 먼저 OS OOM kill을 부를 수 있다. 자동값보다 큰 값을 시험하려면
    # 자동값 자체가 더 큰(=더 큰 RAM) 호스트로 옮긴다.
    MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER: ClassVar[float] = 1.0

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        device: str = "cpu",
        cache: EmbeddingCache | None = None,
        max_chars_per_batch: int | None = None,
        max_length: int | None = None,
    ) -> None:
        """Initialize the BGE-M3 client.

        Args:
            model_path: Directory containing ``model.onnx`` plus tokenizer
                files. Defaults to ``settings.embedding_model_path``.
            device: ``"cpu"`` (default, AVX-VNNI INT8 path) or ``"cuda"``
                (FP16 model on Turing GPUs, INT8 on Tensor-Core GPUs). The
                caller is responsible for pointing ``model_path`` at the
                matching quantization directory; this flag only picks the
                ONNX Runtime providers list.
            cache: Disk cache instance. Defaults to a new
                :class:`EmbeddingCache` rooted at ``settings.embedding_cache_dir``.
            max_chars_per_batch: Override the RAM-derived dynamic batching
                cap. Use for memory-constrained tests or to force a specific
                batch granularity. When None, the cap is auto-tuned from the
                host's total RAM.
            max_length: Override tokenizer truncation length.
        """
        resolved_path = Path(model_path or settings.embedding_model_path)
        self._model_dir = resolved_path
        self._device = device
        # 지문은 모델 디렉터리 이름이다 - int8/fp16 폴더가 갈리므로 dtype까지 구분된다.
        # 이게 없으면 모델을 바꿔도 캐시가 옛 벡터를 그대로 돌려준다(EmbeddingCache 참조).
        self._cache = cache or EmbeddingCache(
            root=settings.embedding_cache_dir, fingerprint=resolved_path.name
        )
        self._max_length = max_length or self.MAX_LENGTH

        # 호스트 RAM에 따라 동적 배치 상한 자동 조정. 외부에서도 같은 값을 참조할 수
        # 있도록 staticmethod로 노출.
        auto_max_chars = self.auto_max_chars_per_batch()

        # override가 자동 안전값의 MULTIPLIER 배를 넘으면 거절. silent clip 대신 raise —
        # 호출자(특히 벤치마크 sweep)는 자기가 설정한 값이 측정에 그대로 들어간다고 가정하기
        # 때문에 조용히 clip하면 결과가 왜곡되고 같은 OOM이 다른 경로로 돌아온다.
        if max_chars_per_batch is not None:
            hard_cap = int(auto_max_chars * self.MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER)
            if max_chars_per_batch > hard_cap:
                total_ram_gb = psutil.virtual_memory().total / 1024**3
                raise ValueError(
                    f"max_chars_per_batch={max_chars_per_batch} exceeds hard cap "
                    f"{hard_cap} (= auto {auto_max_chars} × "
                    f"{self.MAX_CHARS_PER_BATCH_OVERRIDE_MULTIPLIER}). "
                    f"Total RAM={total_ram_gb:.1f}GB. Lower the override or measure "
                    f"on a larger host."
                )
            self._max_chars_per_batch = max_chars_per_batch
        else:
            self._max_chars_per_batch = auto_max_chars

        logger.info(
            "embedding.client.init.started",
            model_path=str(resolved_path),
            max_chars_per_batch=self._max_chars_per_batch,
            max_length=self._max_length,
        )
        # cuda를 요청하면 CUDA EP를 먼저, 실패 시 CPU EP로 ORT가 자동 폴백 — Turing은
        # libcublasLt.so.12 등 cu12 런타임 wheel(`nvidia-cublas-cu12` 등)이 LD_LIBRARY_PATH
        # 에 있어야 실제로 GPU에 올라간다.
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if device == "cuda"
            else ["CPUExecutionProvider"]
        )
        t0 = time.perf_counter()
        # 추론 알맹이는 onnx_text_embedder 한 벌만 쓴다 - GPU 서비스도 같은 파일을
        # import하므로 토크나이즈·풀링·정규화가 양쪽에서 갈라질 수 없다.
        self._embedder = OnnxTextEmbedder(
            resolved_path,
            max_length=self._max_length,
            max_chars_per_batch=self._max_chars_per_batch,
            providers=providers,
        )
        logger.info(
            "embedding.client.init.completed",
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
        )

    @property
    def tokenizer_lock(self) -> threading.Lock:
        """토크나이저 직렬화 락 — 이 토크나이저를 쓰는 모든 곳이 같은 락을 잡아야 한다."""
        return self._embedder.tokenizer_lock

    @property
    def tokenizer(self) -> PreTrainedTokenizerBase:
        """Expose the HF tokenizer for downstream reuse (e.g. token counting).

        Returning the same instance avoids paying the from_pretrained cost
        twice and ensures truncation/special-token handling stays consistent
        across embedding and any tokenizer-based estimators.
        """
        return self._embedder.tokenizer

    @staticmethod
    def auto_max_chars_per_batch() -> int:
        """Return the host-RAM-based default for ``max_chars_per_batch``.

        Three tiers: ≥30 GB hosts get a high throughput setting, 14-30 GB
        hosts (typical workstation) get the safe mid value, and smaller
        hosts (CI containers, low-RAM laptops) get a conservative one.

        Exposed as a staticmethod so callers (e.g. benchmark sweep
        definition) can scale their own parameters to the host without
        duplicating the tier table.
        """
        # 티어 표는 chars_budget_for_bytes 한 곳에만 둔다 - GPU 서비스는 같은 함수에
        # VRAM 여유분을 넣는다. 표가 두 벌이 되면 한쪽만 고쳐지고 다른 쪽이 OOM 난다.
        return chars_budget_for_bytes(psutil.virtual_memory().total)

    @staticmethod
    def _make_dynamic_batches(texts: list[str], max_chars: int) -> list[list[str]]:
        """Group texts into batches whose accumulated char count stays under ``max_chars``.

        A single text exceeding ``max_chars`` becomes its own (oversized) batch —
        tokenizer truncation caps the eventual tensor regardless, so we never
        raise to the caller.

        Args:
            texts: Texts to batch, in the order they should be processed.
            max_chars: Per-batch accumulated character ceiling. Adding a text
                that would push the sum over this value starts a new batch
                (unless the current batch is empty).

        Returns:
            List of batches; each batch is a non-empty list of texts.
        """
        if not texts:
            return []
        batches: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for text in texts:
            if current and current_chars + len(text) > max_chars:
                batches.append(current)
                current = []
                current_chars = 0
            current.append(text)
            current_chars += len(text)
        if current:
            batches.append(current)
        return batches

    async def embed(self, text: str) -> EmbeddingResult:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(
        self,
        texts: list[str],
        *,
        sort_for_padding: bool = True,
    ) -> list[EmbeddingResult]:
        """Embed each text, hitting the disk cache when possible.

        Cache misses are sorted by length (when ``sort_for_padding`` is True),
        grouped into dynamic batches whose accumulated character count stays
        under ``self._max_chars_per_batch``, and sent through ONNX one batch
        per forward pass. Results are returned in the original input order.

        Args:
            texts: Input texts to embed.
            sort_for_padding: When True (default), cache-miss texts are sorted
                by character length before batching so each batch has near-
                uniform sequence lengths. BGE-M3 pads every sequence to the
                batch's longest, so mixed-length inputs make short sequences
                pay for long ones. Disable only to A/B against the sorted path.

        Returns:
            Embeddings in the original input order regardless of
            ``sort_for_padding`` — internal reordering is unwound before
            returning so callers never see permutation.
        """
        if not texts:
            return []

        # OOM 안전 처방: 단일 입력의 글자 수를 모델 단계 이전에 차단. 동일 임베딩이
        # 보장되므로 (MAX_INPUT_CHARS 주석 참조) 캐시 키와 tokenizer 입력 양쪽에 사전
        # cut된 문자열을 사용. EmbeddingResult.text는 호출자가 준 원본을 그대로 보존.
        safe_texts = [
            t[: self.MAX_INPUT_CHARS] if len(t) > self.MAX_INPUT_CHARS else t for t in texts
        ]
        n_truncated = sum(
            1 for orig, safe in zip(texts, safe_texts, strict=True) if orig is not safe
        )

        results: list[EmbeddingResult | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for i, (orig, safe) in enumerate(zip(texts, safe_texts, strict=True)):
            cached_vec = self._cache.load(safe)
            if cached_vec is not None:
                results[i] = EmbeddingResult(
                    embedding=cached_vec.reshape(-1).tolist(),
                    text=orig,
                    cached=True,
                )
            else:
                miss_indices.append(i)
                miss_texts.append(safe)

        hits = len(texts) - len(miss_texts)
        logger.info(
            "embedding.encode.started",
            n_total=len(texts),
            n_cache_hits=hits,
            n_cache_misses=len(miss_texts),
            n_truncated=n_truncated,
            sort_for_padding=sort_for_padding,
            max_chars_per_batch=self._max_chars_per_batch,
        )

        if miss_texts:
            # 정렬 활성 시 미스 텍스트만 글자 수 오름차순으로 재배열한 뒤 encode.
            # miss_indices도 같은 permutation으로 변환해 결과 슬롯 매핑이 깨지지 않게 한다.
            if sort_for_padding:
                order = sorted(range(len(miss_texts)), key=lambda i: len(miss_texts[i]))
                miss_texts = [miss_texts[i] for i in order]
                miss_indices = [miss_indices[i] for i in order]

            batches = self._make_dynamic_batches(miss_texts, self._max_chars_per_batch)
            t0 = time.perf_counter()
            cursor = 0
            for batch_idx, chunk in enumerate(batches):
                # 메모리 압력이 임계 초과 시 강제 GC. ONNX runtime이 직전 forward 텐서를 늦게
                # 푸는 패턴에서 누적 OOM을 막는 안전망.
                mem_percent = psutil.virtual_memory().percent
                if mem_percent > self.MEMORY_PRESSURE_THRESHOLD:
                    logger.warning(
                        "embedding.memory.pressure",
                        batch_idx=batch_idx,
                        total_batches=len(batches),
                        mem_percent=mem_percent,
                    )
                    # 실제 OOM은 SIGKILL이라 Sentry에 아무 흔적도 남지 않는다.
                    # 이 임계 초과가 유일한 사전 신호라 예외가 없어도 명시적으로 올린다.
                    # 프로세스당 첫 1회만 — 이후 배치의 압박은 로그로만 남긴다.
                    global _MEMORY_PRESSURE_REPORTED
                    if not _MEMORY_PRESSURE_REPORTED:
                        _MEMORY_PRESSURE_REPORTED = True
                        with sentry_sdk.new_scope() as scope:
                            scope.set_tag("degradation", "embedding")
                            scope.set_tag("embedding_signal", "memory_pressure")
                            scope.set_extra("mem_percent", mem_percent)
                            scope.set_extra("threshold_percent", self.MEMORY_PRESSURE_THRESHOLD)
                            scope.set_extra("batch_idx", batch_idx)
                            scope.set_extra("total_batches", len(batches))
                            sentry_sdk.capture_message(
                                "embedding memory pressure above threshold (pre-OOM signal); "
                                "further occurrences in this process are logged only",
                                level="warning",
                            )
                    gc.collect()

                # 동기 ONNX 추론을 워커 스레드로 — 이벤트 루프에서 직접 돌리면 색인
                # 내내 API 전체가 얼어붙는다(2026-08-04 실측: 개요 조회 무응답).
                # ONNX runtime은 추론 중 GIL을 놓으므로 스레드 오프로드가 유효하다.
                vectors = await asyncio.to_thread(self._encode, chunk)
                for k, safe_text in enumerate(chunk):
                    vec = vectors[k]
                    # 캐시 키는 안전 cut된 텍스트 — 원본과 동일 임베딩이 보장되므로 cut 기준
                    # 으로 캐시해 prefix가 같은 입력은 한 슬롯에 모인다.
                    self._cache.store(safe_text, vec)
                    idx = miss_indices[cursor + k]
                    results[idx] = EmbeddingResult(
                        embedding=vec.tolist(),
                        text=texts[idx],
                        cached=False,
                    )
                cursor += len(chunk)
            logger.info(
                "embedding.encode.completed",
                n_encoded=len(miss_texts),
                n_batches=len(batches),
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

        # 위 두 분기를 거치면 results의 모든 슬롯이 채워짐 — None 잔존 시 invariant 위반.
        assert all(r is not None for r in results), "embed_batch left a slot unfilled"
        return [r for r in results if r is not None]

    def _encode(self, texts: list[str]) -> np.ndarray:
        """한 번의 ONNX 전방계산 — (N, DIMENSION) L2 정규화 벡터.

        배치 분할은 호출부(embed_batch)가 이미 했으므로 여기서는 그대로 넘긴다.
        """
        return self._embedder.encode_batch(texts)
