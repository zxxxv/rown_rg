"""웹 리서치 출처 수집 PoC 검증 엔트리포인트.

서비스(WebResearchService)를 구동해 수집한 출처를 디렉터리로 덤프하고 육안 검증한다.

Usage:
    uv run python scripts/web_research_poc.py scripts/_research_input.example.json -o output/
    uv run python scripts/web_research_poc.py <input.json> -o output/ --model claude-opus-4-8
    uv run python scripts/web_research_poc.py <input.json> --allow "*.go.kr,*.or.kr"
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from src.core.asyncio_compat import configure_event_loop
from src.services.research import ResearchSpec, WebResearchService

configure_event_loop()


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value).strip("_")[:40] or "research"


async def main() -> int:
    parser = argparse.ArgumentParser(description="웹 리서치 출처 수집 PoC")
    parser.add_argument("input", help="topic/report_type/outline JSON 파일 경로")
    parser.add_argument("-o", "--out", default="output", help="출력 디렉터리 (기본 output/)")
    parser.add_argument("--model", default=None, help="모델 ID (기본 claude-sonnet-4-6)")
    parser.add_argument("--allow", default=None, help="허용 도메인 콤마구분 (예: *.go.kr,*.or.kr)")
    args = parser.parse_args()

    spec = ResearchSpec.model_validate_json(Path(args.input).read_text(encoding="utf-8"))
    allowed = [d.strip() for d in args.allow.split(",")] if args.allow else None

    service = WebResearchService()
    kwargs = {"allowed_domains": allowed}
    if args.model:
        kwargs["model"] = args.model
    result = await service.collect(spec, **kwargs)

    out_dir = Path(args.out) / _slug(spec.topic)
    sources_dir = out_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    body_count = 0
    for i, src in enumerate(result.sources, 1):
        if not src.content_md:
            continue
        body_count += 1
        header = (
            f"# {src.title or src.url}\n\n"
            f"- URL: {src.url}\n"
            f"- reliability: {src.reliability}\n"
            f"- sections: {', '.join(src.matched_sections) or '-'}\n"
            f"- page_age: {src.page_age or '-'}\n\n---\n\n"
        )
        path = sources_dir / f"{i:02d}_{_slug(src.title or src.url)}.md"
        path.write_text(header + src.content_md, encoding="utf-8")

    (out_dir / "manifest.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    print(f"수집 출처: {len(result.sources)}개 (본문 있음: {body_count}개)", file=sys.stderr)
    if result.coverage_gaps:
        print(f"⚠ 출처 부족 섹션: {result.coverage_gaps}", file=sys.stderr)
    print(f"저장 위치: {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
