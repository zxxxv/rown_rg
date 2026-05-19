"""Profile chunking on a pre-parsed hwpx markdown — find where the wall clock goes.

Reads /tmp/bench_inputs/hwpx.md, runs ``ChunkingService.chunk_markdown`` once,
and instruments ``BgeM3Client.embed_batch`` to record call count, batch size,
total embed time, and per-call wall clock. Output is printed live so a
thermal-shortened run still leaves useful evidence.

Usage:
    uv run python scripts/chunking_profile.py
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import uuid4

from src.clients.embedding_client import BgeM3Client
from src.services.indexing._chunking import ChunkingService

INPUT_MD = Path("/tmp/bench_inputs/hwpx.md")


async def amain() -> None:
    md = INPUT_MD.read_text()
    print(f"markdown : {len(md):,} chars", flush=True)

    t_init = time.perf_counter()
    client = BgeM3Client()
    print(f"client   : loaded in {(time.perf_counter() - t_init):.1f}s", flush=True)

    # embed_batch 호출마다 진행 상황을 stdout으로 — 중간 종료돼도 흔적 남기기.
    call_no = 0
    total_calls = 0
    total_texts = 0
    total_embed_s = 0.0

    original_embed = client.embed_batch

    async def traced_embed_batch(texts, **kw):
        nonlocal call_no, total_calls, total_texts, total_embed_s
        n = call_no
        call_no += 1
        total_calls += 1
        total_texts += len(texts)
        avg_len = sum(len(t) for t in texts) // max(len(texts), 1)
        t0 = time.perf_counter()
        result = await original_embed(texts, **kw)
        dt = time.perf_counter() - t0
        total_embed_s += dt
        print(
            f"  [#{n:04d}] {len(texts):4d} texts, avg {avg_len:5d} chars, {dt * 1000:7.0f}ms",
            flush=True,
        )
        return result

    client.embed_batch = traced_embed_batch  # type: ignore[method-assign]

    svc = ChunkingService(embedding_client=client)
    print(f"service  : ready (breakpoint_amount={svc._breakpoint_amount})", flush=True)
    print()
    print("=== chunking ===", flush=True)

    t_wall = time.perf_counter()
    chunks = await svc.chunk_markdown(md, uuid4())
    elapsed = time.perf_counter() - t_wall

    print()
    print("=== summary ===", flush=True)
    print(f"chunks produced  : {len(chunks)}")
    print(f"wall clock       : {elapsed:.1f}s")
    print(f"embed_batch calls: {total_calls}")
    print(f"embed_batch texts: {total_texts}")
    print(
        f"embed time       : {total_embed_s:.1f}s "
        f"({total_embed_s / elapsed * 100:.0f}% of wall)"
    )
    if total_calls:
        print(f"avg per call     : {total_embed_s / total_calls * 1000:.0f}ms")
        print(f"avg batch size   : {total_texts / total_calls:.1f}")


if __name__ == "__main__":
    asyncio.run(amain())
