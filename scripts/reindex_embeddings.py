"""저장된 임베딩을 현재 임베딩 클라이언트로 다시 만든다.

**언제 쓰나.** 임베딩 모델이나 dtype이 바뀌었을 때. 벡터는 텍스트만으로 정해지지
않고 어느 모델을 어느 dtype으로 돌렸는지가 같이 정한다. 색인에 두 공간의 벡터가
섞이면 **에러 없이 검색만 나빠진다** — 그래서 바꿨으면 전부 다시 만들어야 한다.

**무엇을 다시 만드나.** ``chunks.embedding``과 ``raptor_nodes.embedding`` 둘 다.
raptor 노드도 벡터로 검색되므로 청크만 바꾸면 절반이 옛 공간에 남는다.

**트리 구조는 다시 세우지 않는다.** ``RaptorTreeBuilder``는 청크 임베딩을 k-means로
묶어 트리를 만들므로, 엄밀히는 임베딩이 바뀌면 묶음도 달라질 수 있다. 그런데 재구축은
클러스터마다 **LLM 요약을 다시 부르는** 일이라 비용이 실제로 든다. dtype만 바뀐
경우(int8 → fp16)는 벡터가 거의 같은 방향이라 묶음이 사실상 그대로다 — 요약문은
여전히 유효하고, 그 요약문의 임베딩만 새 공간으로 옮기면 검색 정합성은 회복된다.
공간이 크게 달라졌다면(export 스크립트의 ``--verify``에서 코사인이 0.99 아래) 그때는
트리 재구축을 따로 검토할 것.

    # 무엇을 할지만 보기 (기본)
    uv run python scripts/reindex_embeddings.py

    # 실제 실행
    uv run python scripts/reindex_embeddings.py --apply

    # 중단됐다면 마지막으로 끝난 id 다음부터
    uv run python scripts/reindex_embeddings.py --apply --after-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.clients.embedding_factory import get_embedding_client
from src.db.session import async_session_maker

# 한 번에 DB에서 읽고 임베딩할 행 수. 원격 클라이언트가 내부적으로 다시 잘라
# 보내므로(embedding_remote_chunk) 여기서는 트랜잭션 크기만 정한다. 너무 크면
# 중단 시 잃는 진행분이 커지고, 너무 작으면 왕복이 늘어난다.
BATCH = 256

TARGETS = {
    "chunks": ("chunks", "content", "임베딩된 자료 조각"),
    "raptor": ("raptor_nodes", "summary", "RAPTOR 요약 노드"),
}


async def count_rows(session, table: str, column: str, after_id: str | None) -> int:
    where = f"WHERE {column} IS NOT NULL AND {column} <> ''"
    if after_id:
        where += " AND id > :after"
    q = text(f"SELECT count(*) FROM {table} {where}")  # noqa: S608 - 테이블명은 상수 표에서만 온다
    result = await session.execute(q, {"after": after_id} if after_id else {})
    return int(result.scalar_one())


async def reindex_table(
    table: str, column: str, label: str, *, apply: bool, after_id: str | None
) -> int:
    client = get_embedding_client()
    done = 0
    t_start = time.perf_counter()

    async with async_session_maker() as session:
        total = await count_rows(session, table, column, after_id)
        print(f"\n=== {label} ({table}) — 대상 {total}건 ===")
        if total == 0:
            return 0

        cursor = after_id
        while True:
            where = f"WHERE {column} IS NOT NULL AND {column} <> ''"
            params: dict = {"limit": BATCH}
            if cursor:
                where += " AND id > :after"
                params["after"] = cursor
            # id 순서로 훑는다 - 중단돼도 --after-id 로 이어붙일 수 있다.
            rows = (
                await session.execute(
                    text(  # noqa: S608 - 테이블/컬럼명은 상수 표에서만 온다
                        f"SELECT id, {column} AS body FROM {table} {where} "
                        f"ORDER BY id LIMIT :limit"
                    ),
                    params,
                )
            ).all()
            if not rows:
                break

            texts = [r.body for r in rows]
            if apply:
                results = await client.embed_batch(texts)
                # 순서가 1:1이라는 계약에 의존한다. 원격 클라이언트가 개수를 검증하지만
                # 여기서도 한 번 더 본다 - 어긋난 채로 UPDATE가 나가면 되돌릴 수 없다.
                if len(results) != len(rows):
                    raise RuntimeError(f"임베딩 개수 불일치: {len(results)} != {len(rows)}")
                for row, res in zip(rows, results, strict=True):
                    await session.execute(
                        text(f"UPDATE {table} SET embedding = :emb WHERE id = :id"),  # noqa: S608
                        {"emb": str(res.embedding), "id": row.id},
                    )
                await session.commit()

            done += len(rows)
            cursor = rows[-1].id
            elapsed = time.perf_counter() - t_start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            print(
                f"  {done}/{total}  ({done * 100 // total}%)  "
                f"{rate:.1f}건/초  남은 시간 약 {eta / 60:.1f}분  마지막 id={cursor}"
            )
    return done


async def amain(args) -> None:
    client = get_embedding_client()
    print(f"임베딩 클라이언트: {type(client).__name__}")
    if not args.apply:
        print("** 미리보기입니다. 실제로 쓰려면 --apply 를 붙이세요. **")

    total = 0
    for key in args.targets:
        table, column, label = TARGETS[key]
        total += await reindex_table(
            table, column, label, apply=args.apply, after_id=args.after_id
        )
    print(f"\n합계 {total}건 {'갱신' if args.apply else '대상 확인'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=sorted(TARGETS),
        default=sorted(TARGETS),
        help="다시 만들 대상 (기본: 전부)",
    )
    # 기본이 미리보기인 이유: 이 스크립트는 색인을 통째로 덮어쓴다. 잘못된 클라이언트
    # 설정으로 무심코 돌리면 옛 공간의 벡터로 되돌려 놓을 수도 있다.
    parser.add_argument("--apply", action="store_true", help="실제로 DB에 쓴다")
    parser.add_argument("--after-id", help="이 id 다음부터 (중단 후 이어서)")
    args = parser.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
