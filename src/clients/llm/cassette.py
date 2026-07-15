import asyncio
import hashlib
import json
import re
from pathlib import Path

from src.clients.llm.base import CompletionRequest, CompletionResponse
from src.clients.llm.exceptions import CassetteNotFoundError
from src.core.clock import now

_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def compute_input_hash(request: CompletionRequest) -> str:
    # web_search는 live 모드 전용(외부 실시간 데이터, 캐셋 미사용)이므로 해시에서 제외해
    # 기존 단발 호출 캐셋이 무효화되지 않게 한다.
    payload = request.model_dump_json(exclude={"cache_key", "web_search"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_cache_key(request: CompletionRequest, input_hash: str) -> str:
    return request.cache_key or input_hash[:16]


def _slug(value: str) -> str:
    return _SLUG_RE.sub("_", value).strip("_") or "default"


class CassetteManager:
    def __init__(self, cassette_dir: Path):
        self.cassette_dir = Path(cassette_dir)
        self.cassette_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    def _path(self, operation: str, cache_key: str, input_hash: str) -> Path:
        filename = f"{_slug(operation)}_{_slug(cache_key)}_{input_hash[:8]}.json"
        return self.cassette_dir / filename

    def _get_lock(self, path: Path) -> asyncio.Lock:
        key = str(path)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def exists(self, operation: str, cache_key: str, input_hash: str) -> bool:
        return self._path(operation, cache_key, input_hash).exists()

    def load(self, operation: str, cache_key: str, input_hash: str) -> CompletionResponse:
        path = self._path(operation, cache_key, input_hash)
        if not path.exists():
            raise CassetteNotFoundError(f"Cassette not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return CompletionResponse(**data["response"])

    async def save(
        self,
        operation: str,
        cache_key: str,
        input_hash: str,
        request: CompletionRequest,
        response: CompletionResponse,
    ) -> Path:
        path = self._path(operation, cache_key, input_hash)
        cassette = {
            "version": "1.0",
            "cache_key": cache_key,
            "input_hash": input_hash,
            "request": request.model_dump(),
            "response": response.model_dump(),
            "recorded_at": now().isoformat(),
        }
        async with self._get_lock(path):
            path.write_text(
                json.dumps(cassette, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return path
