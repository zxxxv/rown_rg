"""Bulk-index a directory through the vector RAG pipeline.

Walks a directory recursively, dispatches each supported file
(``.hwpx`` / ``.pdf``) through :class:`VectorIndexingService`, and emits a
markdown report with per-file stage timings.

Usage:
    uv run python scripts/index_directory.py ./test_data/ \
        --project-id 11111111-1111-1111-1111-111111111111 \
        --parallel 4 --report

See ``reports/index_directory_inspection.md`` for the design decisions
behind ``--force`` cache invalidation, library-node flat policy, and the
dry-run shortcut path.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from tqdm import tqdm

from src.clients.embedding_client import BgeM3Client
from src.clients.parser import ParseCache, ParserRegistry, UnsupportedFormatError
from src.db.models.library_node import LibraryNode
from src.db.session import async_engine, async_session_maker
from src.services.indexing._chunking import ChunkingService
from src.services.indexing.vector import SourceInput, VectorIndexingService

logger = structlog.get_logger(__name__)

# PDF는 docling 풀로드 시 CPU·thermal 한계로 로컬 노트북에선 강제 종료 위험 — 옵트인
HWPX_EXTENSIONS = {".hwpx"}
PDF_EXTENSIONS = {".pdf"}
REPORT_DIR = Path("./reports")


class _Timing:
    """Per-file stage timing accumulator — populated by the service via logs would
    be cleaner, but a simple wrapper around index_source keeps this script
    self-contained."""

    def __init__(self) -> None:
        self.parse_ms: float = 0.0
        self.chunk_ms: float = 0.0
        self.embed_ms: float = 0.0
        self.store_ms: float = 0.0
        self.total_ms: float = 0.0


def _collect_files(root: Path, *, include_pdf: bool) -> tuple[list[Path], list[Path]]:
    """Return (supported_files, skipped_files) under ``root`` recursively.

    PDFs are included only when ``include_pdf=True`` — docling 부하 때문에
    기본 차단이고, GPU·고메모리 호스트에서만 ``--include-pdf``로 열어준다.
    """
    allowed = HWPX_EXTENSIONS | (PDF_EXTENSIONS if include_pdf else set())
    supported: list[Path] = []
    skipped: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in allowed:
            supported.append(path)
        else:
            skipped.append(path)
    return supported, skipped


def _invalidate_cache(path: Path) -> None:
    """Best-effort delete of the parse cache entry for ``path`` (for ``--force``)."""
    cache = ParseCache()
    try:
        key = cache._key(path)
    except FileNotFoundError:
        return
    cache_file = cache._path_for(key)
    if cache_file.exists():
        cache_file.unlink()


async def _resolve_library_leaf(session_maker, parent_id: UUID, file_path: Path) -> UUID:
    """Find-or-create a flat library_node leaf for ``file_path`` under ``parent_id``."""
    async with session_maker() as session:
        existing = await session.execute(
            select(LibraryNode).where(
                LibraryNode.parent_id == parent_id,
                LibraryNode.name == file_path.name,
                LibraryNode.type == "file",
            )
        )
        node = existing.scalar_one_or_none()
        if node is not None:
            return node.id

        node = LibraryNode(
            parent_id=parent_id,
            name=file_path.name,
            type="file",
            file_path=str(file_path.resolve()),
            mime_type="application/octet-stream",
        )
        session.add(node)
        await session.commit()
        await session.refresh(node)
        return node.id


async def _index_one(
    file_path: Path,
    *,
    service: VectorIndexingService,
    parser_registry: ParserRegistry,
    chunking_service: ChunkingService,
    project_id: UUID,
    track: str,
    library_root_id: UUID | None,
    session_maker,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Index one file end-to-end. Returns a row dict for the report."""
    timing = _Timing()
    warnings: list[str] = []
    chunks_created = 0

    if force:
        _invalidate_cache(file_path)

    t_start = time.perf_counter()
    try:
        if dry_run:
            t_parse_start = time.perf_counter()
            parse_result = await parser_registry.parse(file_path)
            timing.parse_ms = (time.perf_counter() - t_parse_start) * 1000

            t_chunk_start = time.perf_counter()
            chunks = await chunking_service.chunk_markdown(parse_result.markdown, uuid4())
            timing.chunk_ms = (time.perf_counter() - t_chunk_start) * 1000
            chunks_created = len(chunks)
            warnings = list(parse_result.warnings)
        else:
            if library_root_id is not None:
                leaf_id = await _resolve_library_leaf(session_maker, library_root_id, file_path)
                source = SourceInput(
                    project_id=project_id,
                    source_type="library",
                    file_path=file_path,
                    library_node_id=leaf_id,
                    track=track,  # type: ignore[arg-type]
                    title=file_path.name,
                )
            else:
                source = SourceInput(
                    project_id=project_id,
                    source_type="upload",
                    file_path=file_path,
                    upload_path=str(file_path.resolve()),
                    track=track,  # type: ignore[arg-type]
                    title=file_path.name,
                )
            result = await service.index_source(source)
            chunks_created = result.chunks_created
            # service의 단계별 ms를 직접 노출하지 않으니 총합으로 대신 기록.
            timing.total_ms = result.elapsed_ms
        timing.total_ms = (time.perf_counter() - t_start) * 1000
        status = "ok"
    except UnsupportedFormatError as e:
        status = "skipped"
        warnings.append(str(e))
        timing.total_ms = (time.perf_counter() - t_start) * 1000
    except Exception as e:
        status = "error"
        warnings.append(f"{type(e).__name__}: {e}")
        timing.total_ms = (time.perf_counter() - t_start) * 1000

    return {
        "file": file_path.name,
        "format": file_path.suffix.lower().lstrip("."),
        "chunks": chunks_created,
        "parse_ms": round(timing.parse_ms, 1),
        "chunk_ms": round(timing.chunk_ms, 1),
        "total_ms": round(timing.total_ms, 1),
        "warnings": " | ".join(warnings) if warnings else "-",
        "status": status,
    }


def _format_report(
    rows: list[dict[str, Any]], skipped: list[Path], args: argparse.Namespace
) -> str:
    header = (
        "| 파일 | 포맷 | 청크 | 파싱ms | 청킹ms | 총ms | 상태 | warnings |\n"
        "|------|------|------|--------|--------|------|------|----------|\n"
    )
    body_lines = [
        f"| {r['file']} | {r['format']} | {r['chunks']} | {r['parse_ms']} "
        f"| {r['chunk_ms']} | {r['total_ms']} | {r['status']} | {r['warnings']} |"
        for r in rows
    ]
    totals = (
        f"| **합계** | - | {sum(r['chunks'] for r in rows)} | "
        f"{round(sum(r['parse_ms'] for r in rows), 1)} | "
        f"{round(sum(r['chunk_ms'] for r in rows), 1)} | "
        f"{round(sum(r['total_ms'] for r in rows), 1)} | - | - |"
    )
    skipped_block = (
        "\n\n**미지원 확장자 (skip):**\n" + "\n".join(f"- {p.name}" for p in skipped)
        if skipped
        else ""
    )
    return (
        f"# 일괄 인덱싱 리포트\n\n"
        f"- 대상 디렉토리: `{args.directory}`\n"
        f"- project_id: `{args.project_id}`\n"
        f"- track: `{args.track}`\n"
        f"- parallel: {args.parallel}\n"
        f"- dry_run: {args.dry_run}\n"
        f"- force: {args.force}\n"
        f"- include_pdf: {args.include_pdf}\n"
        f"- library_root_node_id: {args.library_root_node_id or '(none — upload mode)'}\n\n"
        + header
        + "\n".join(body_lines)
        + "\n"
        + totals
        + skipped_block
        + "\n"
    )


async def _run(args: argparse.Namespace) -> int:
    root = Path(args.directory)
    if not root.exists():
        print(f"ERROR: directory not found: {root}", file=sys.stderr)
        return 1

    files, skipped = _collect_files(root, include_pdf=args.include_pdf)
    if not files:
        allowed = HWPX_EXTENSIONS | (PDF_EXTENSIONS if args.include_pdf else set())
        print(f"no supported files under {root} (extensions: {sorted(allowed)})")
        return 0

    parser_registry = ParserRegistry()

    # BGE-M3 무거운 로드를 dry-run에서도 한 번은 한다 — chunking이 임베딩 기반 boundary
    # 판정에 의존하므로. cache hit가 많으면 후속 비용은 거의 0.
    embedding_client = BgeM3Client()
    chunking_service = ChunkingService(embedding_client=embedding_client)

    service: VectorIndexingService | None = None
    if not args.dry_run:
        service = VectorIndexingService(
            parser_registry=parser_registry,
            chunking_service=chunking_service,
            embedding_client=embedding_client,
            session_maker=async_session_maker,
        )

    sem = asyncio.Semaphore(args.parallel)

    async def _task(p: Path) -> dict[str, Any]:
        async with sem:
            return await _index_one(
                p,
                service=service,  # type: ignore[arg-type]
                parser_registry=parser_registry,
                chunking_service=chunking_service,
                project_id=UUID(args.project_id),
                track=args.track,
                library_root_id=UUID(args.library_root_node_id)
                if args.library_root_node_id
                else None,
                session_maker=async_session_maker,
                force=args.force,
                dry_run=args.dry_run,
            )

    rows: list[dict[str, Any]] = []
    with tqdm(total=len(files), desc="indexing") as pbar:
        for coro in asyncio.as_completed([_task(p) for p in files]):
            row = await coro
            rows.append(row)
            pbar.set_postfix(file=row["file"][:30], chunks=row["chunks"])
            pbar.update(1)

    rows.sort(key=lambda r: r["file"])
    report_text = _format_report(rows, skipped, args)

    if args.report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = REPORT_DIR / f"indexing_{ts}.md"
        report_path.write_text(report_text, encoding="utf-8")
        print(f"\n리포트: {report_path}")
    else:
        print("\n" + report_text)

    n_errors = sum(1 for r in rows if r["status"] == "error")
    return 1 if n_errors else 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bulk index a directory through vector RAG.")
    p.add_argument("directory", type=str, help="root directory to walk")
    p.add_argument("--project-id", required=True, help="target project UUID")
    p.add_argument("--track", default="content", choices=("content", "style"))
    p.add_argument("--parallel", type=int, default=1, help="concurrent file count")
    p.add_argument("--dry-run", action="store_true", help="parse+chunk only, no DB writes")
    p.add_argument("--force", action="store_true", help="invalidate parse cache before parsing")
    p.add_argument(
        "--report",
        action="store_true",
        help="write report to ./reports/indexing_{ts}.md instead of stdout",
    )
    p.add_argument(
        "--library-root-node-id",
        default=None,
        help="parent library node UUID — if given, files are registered as library sources",
    )
    p.add_argument(
        "--include-pdf",
        action="store_true",
        help=(
            "also process .pdf (docling). Disabled by default — heavy CPU "
            "load risks thermal shutdown on low-power hosts."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_run(args))
    finally:
        asyncio.run(async_engine.dispose())


if __name__ == "__main__":
    sys.exit(main())
