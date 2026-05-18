"""파싱 결과 사람 검수용 CLI.

단일 모드:
    uv run python scripts/parse_check.py test_data/hwpx/sample.hwpx

일괄 모드:
    uv run python scripts/parse_check.py test_data/ --batch

옵션:
    --no-cache         캐시를 무시하고 항상 새로 파싱
    --output-dir PATH  마크다운 저장 위치 (기본 ./test_data/parsed/)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from src.clients.parser_client import (
    HwpxParser,
    ParseCache,
    ParserClient,
    ParseResult,
    ParserRegistry,
    PdfParser,
    UnsupportedFormatError,
)
from src.core.logging import configure_logging

logger = structlog.get_logger(__name__)

DEFAULT_OUTPUT_DIR = Path("./test_data/parsed")
SUPPORTED_EXTS = {".hwpx", ".pdf"}

_FNAME_BAD = re.compile(r"[^\w가-힣.\-]+", re.UNICODE)
_WORD = re.compile(r"\S+")


class _NoCache(ParseCache):
    """`--no-cache` 시 사용. 항상 미스, 저장 안 함."""

    def load(self, file_path: Path) -> ParseResult | None:
        return None

    def store(self, file_path: Path, result: ParseResult) -> None:
        return


def sanitize_filename(stem: str) -> str:
    out = _FNAME_BAD.sub("_", stem)
    out = re.sub(r"_+", "_", out).strip("_")
    return out or "untitled"


def classify_warning(w: str) -> str:
    if w.endswith("_unavailable"):
        return "unavailable"
    if w.endswith("_deferred"):
        return "deferred"
    if w.startswith("remove_header_failed") or w.startswith("remove_footer_failed"):
        return "header_footer_strip_failed"
    if w.startswith("exporter_failed"):
        return "fallback"
    return "other"


def build_registry(no_cache: bool) -> ParserRegistry:
    cache: ParseCache = _NoCache() if no_cache else ParseCache()
    return ParserRegistry([HwpxParser(cache=cache), PdfParser()])


def write_markdown(
    source_path: Path,
    result: ParseResult,
    output_dir: Path,
    parser_name: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / (sanitize_filename(source_path.stem) + ".md")
    meta = result.metadata
    frontmatter = "\n".join(
        [
            "---",
            f"source: {source_path.name}",
            f"parsed_at: {datetime.now(UTC).isoformat()}",
            f"parser: {parser_name}",
            f"char_count: {meta.char_count}",
            f"table_count: {meta.table_count}",
            f"heading_count: {meta.heading_count}",
            f"header_footer_removed: {meta.header_footer_removed}",
            f"warnings: {json.dumps(result.warnings, ensure_ascii=False)}",
            "---",
            "",
        ]
    )
    out_path.write_text(frontmatter + result.markdown, encoding="utf-8")
    return out_path


def print_single_summary(
    source_path: Path,
    result: ParseResult,
    duration_ms: float,
    out_path: Path,
    parser_name: str,
) -> None:
    meta = result.metadata
    word_count = sum(1 for _ in _WORD.finditer(result.markdown))
    size_bytes = source_path.stat().st_size

    grouped: dict[str, list[str]] = {}
    for w in result.warnings:
        grouped.setdefault(classify_warning(w), []).append(w)

    print()
    print("=" * 72)
    print(f"파일       : {source_path}")
    print(f"포맷       : {source_path.suffix.lower()}")
    print(f"크기       : {size_bytes:,} bytes")
    print(f"파서       : {parser_name}")
    print(f"파싱 시간   : {duration_ms:.1f} ms{' (캐시 적중)' if result.cached else ''}")
    print("-" * 72)
    print(f"마크다운 길이 : {meta.char_count:,} chars / {word_count:,} words")
    print(f"표         : {meta.table_count}")
    print(f"헤더       : {meta.heading_count}")
    print(f"H/F 제거    : {meta.header_footer_removed}")
    print(f"page_count : {meta.page_count}")
    if grouped:
        print("-" * 72)
        print("warnings:")
        for kind, items in grouped.items():
            for w in items:
                print(f"  [{kind}] {w}")
    print("-" * 72)
    print("마크다운 앞 500자:")
    preview = result.markdown[:500]
    print(preview + ("..." if len(result.markdown) > 500 else ""))
    print("=" * 72)
    print(f"저장 위치  : {out_path}")
    print()


def collect_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)


def render_batch_table(rows: list[dict[str, Any]]) -> str:
    headers = ["파일", "포맷", "시간(ms)", "길이(자)", "표", "헤더", "H/F제거", "warnings"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    r["name"],
                    r["ext"],
                    r["duration_ms"],
                    r["char_count"],
                    r["table_count"],
                    r["heading_count"],
                    r["hf_removed"],
                    r["warnings"],
                ]
            )
            + " |"
        )
    return "\n".join(lines)


async def _parse_one(
    parser: ParserClient,
    file_path: Path,
) -> tuple[ParseResult | None, float, str | None]:
    t0 = time.perf_counter()
    try:
        result = await parser.parse(file_path)
        return result, (time.perf_counter() - t0) * 1000, None
    except NotImplementedError:
        return None, (time.perf_counter() - t0) * 1000, "stub"
    except Exception as e:
        logger.exception(
            "parse_check.parse_failed",
            path=str(file_path),
            error_type=type(e).__name__,
        )
        return None, (time.perf_counter() - t0) * 1000, f"error:{type(e).__name__}"


async def run_single(file_path: Path, registry: ParserRegistry, output_dir: Path) -> int:
    try:
        parser = registry.resolve(file_path)
    except UnsupportedFormatError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    parser_name = type(parser).__name__
    logger.info("parse_check.single.started", path=str(file_path), parser=parser_name)

    result, duration_ms, err = await _parse_one(parser, file_path)
    if result is None:
        print(f"[ERROR] 파싱 실패: {file_path} ({err})", file=sys.stderr)
        return 1

    out_path = write_markdown(file_path, result, output_dir, parser_name)
    print_single_summary(file_path, result, duration_ms, out_path, parser_name)
    return 0


async def run_batch(directory: Path, registry: ParserRegistry, output_dir: Path) -> int:
    files = collect_files(directory)
    if not files:
        print(f"[WARN] 대상 파일 없음: {directory}", file=sys.stderr)
        return 1

    logger.info("parse_check.batch.started", root=str(directory), n_files=len(files))

    rows: list[dict[str, Any]] = []
    for file_path in files:
        try:
            parser = registry.resolve(file_path)
        except UnsupportedFormatError:
            continue
        parser_name = type(parser).__name__
        result, duration_ms, err = await _parse_one(parser, file_path)
        rel_name = file_path.name

        if result is None:
            rows.append(
                {
                    "name": rel_name,
                    "ext": file_path.suffix.lower(),
                    "duration_ms": f"{duration_ms:.0f}",
                    "char_count": "-",
                    "table_count": "-",
                    "heading_count": "-",
                    "hf_removed": "-",
                    "warnings": err or "error",
                }
            )
            continue

        write_markdown(file_path, result, output_dir, parser_name)
        meta = result.metadata
        rows.append(
            {
                "name": rel_name,
                "ext": file_path.suffix.lower(),
                "duration_ms": f"{duration_ms:.0f}{'*' if result.cached else ''}",
                "char_count": f"{meta.char_count:,}",
                "table_count": str(meta.table_count),
                "heading_count": str(meta.heading_count),
                "hf_removed": str(meta.header_footer_removed),
                "warnings": ",".join(sorted({classify_warning(w) for w in result.warnings})) or "-",
            }
        )

    print()
    print(render_batch_table(rows))
    print()
    print(f"저장 위치: {output_dir} (시간 컬럼의 * 표시는 캐시 적중)")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HWPX/PDF 파싱 결과 검수용 CLI")
    p.add_argument("path", type=Path, help="단일 파일 또는 디렉토리")
    p.add_argument("--batch", action="store_true", help="디렉토리를 재귀 일괄 파싱")
    p.add_argument("--no-cache", action="store_true", help="캐시 무시")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"마크다운 저장 위치 (기본 {DEFAULT_OUTPUT_DIR})",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()

    if not args.path.exists():
        print(f"[ERROR] 경로 없음: {args.path}", file=sys.stderr)
        return 2

    registry = build_registry(no_cache=args.no_cache)

    if args.batch:
        if not args.path.is_dir():
            print(f"[ERROR] --batch는 디렉토리 인자가 필요: {args.path}", file=sys.stderr)
            return 2
        return asyncio.run(run_batch(args.path, registry, args.output_dir))

    if args.path.is_dir():
        print("[ERROR] 디렉토리 입력은 --batch 와 함께 사용", file=sys.stderr)
        return 2
    return asyncio.run(run_single(args.path, registry, args.output_dir))


if __name__ == "__main__":
    raise SystemExit(main())
