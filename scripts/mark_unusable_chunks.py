"""근거로 못 쓰는 청크 표시 — 보일러플레이트 + 내용 중복.

검색에서만 빼고 지우지는 않는다(metadata.excluded에 이유를 남긴다). 원문 대조 화면은
모델이 실제로 받은 것을 그대로 보여줘야 하므로 행을 없애면 안 된다.

두 종류를 표시한다.
- 보일러플레이트: 사이트 메뉴·그림 껍데기·목차·참고문헌·깨진 인코딩(services/indexing/_boilerplate)
- 내용 중복: 같은 프로젝트에서 본문이 완전히 같은 청크. 같은 문서를 웹 수집과 파일
  업로드로 두 번 넣으면 생긴다(실측: 예타 프로젝트에서 54청크 + 55청크 두 벌).
  가장 먼저 만들어진 것만 남기고 나머지를 표시한다.

사용:
    uv run python scripts/mark_unusable_chunks.py [--dry-run] [--project-id UUID] [--undo]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import text

from src.db.session import async_session_maker
from src.services.indexing._boilerplate import boilerplate_kind


async def main() -> int:
    parser = argparse.ArgumentParser(description="근거로 못 쓰는 청크 표시")
    parser.add_argument("--dry-run", action="store_true", help="분포만 보고 DB는 안 건드림")
    parser.add_argument("--project-id", help="특정 프로젝트만")
    parser.add_argument("--undo", action="store_true", help="표시 전부 해제")
    args = parser.parse_args()

    where = "WHERE project_id = :pid" if args.project_id else ""
    params = {"pid": args.project_id} if args.project_id else {}

    async with async_session_maker() as session:
        if args.undo:
            result = await session.execute(
                text(
                    "UPDATE chunks SET metadata = metadata - 'excluded'"
                    f" {where or 'WHERE true'} AND metadata ? 'excluded'"
                ),
                params,
            )
            await session.commit()
            print(f"표시 해제 {result.rowcount}건")
            return 0

        rows = (
            await session.execute(
                text(f"SELECT id, project_id, content, created_at FROM chunks {where}"), params
            )
        ).all()

    seen: dict[tuple, str] = {}
    marks: dict[str, str] = {}
    for cid, pid, content, _created in sorted(rows, key=lambda r: (str(r[1]), r[3])):
        kind = boilerplate_kind(content or "")
        if kind:
            marks[str(cid)] = kind
            continue
        key = (str(pid), hash(content))
        if key in seen:
            marks[str(cid)] = "내용 중복"
        else:
            seen[key] = str(cid)

    dist = Counter(marks.values())
    total = len(rows)
    print(f"대상 {total:,}청크 중 표시 {len(marks):,}건 ({len(marks) / max(total, 1):.1%})")
    for kind, n in dist.most_common():
        print(f"  {kind:12s} {n:5,}건 {n / max(total, 1):5.1%}")

    if args.dry_run:
        print("[dry-run] DB를 바꾸지 않았습니다.")
        return 0

    async with async_session_maker() as session:
        for cid, kind in marks.items():
            await session.execute(
                text(
                    "UPDATE chunks SET metadata = coalesce(metadata, '{}'::jsonb)"
                    " || jsonb_build_object('excluded', cast(:kind as text))"
                    " WHERE id = cast(:cid as uuid)"
                ),
                {"cid": cid, "kind": kind},
            )
        await session.commit()
    print(f"표시 완료 {len(marks):,}건 (되돌리려면 --undo)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
