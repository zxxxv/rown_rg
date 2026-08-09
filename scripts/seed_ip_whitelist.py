"""Seed an IP whitelist entry so production traffic isn't blocked.

프로덕션(ENVIRONMENT != local)에선 IPWhitelistMiddleware 가 활성화되어, ip_whitelist
테이블에 매칭되는 행이 없으면 /health·/docs 를 제외한 모든 요청을 403 으로 막는다.
최초 배포 직후 사람이 로그인할 수 있도록 최소 1행을 넣는다.

Usage:
    # 기본값: 0.0.0.0/0 (전체 허용 — 초기 오픈용, 이후 admin UI 에서 좁힐 것)
    uv run python scripts/seed_ip_whitelist.py

    # 특정 대역만 허용
    IP_WHITELIST_CIDR=203.0.113.0/24 uv run python scripts/seed_ip_whitelist.py

    # 여러 개(콤마 구분). IPv6 클라이언트까지 커버하려면 ::/0 도 함께.
    IP_WHITELIST_CIDR="0.0.0.0/0,::/0" uv run python scripts/seed_ip_whitelist.py

같은 CIDR 이 이미 있으면 건너뛴다(idempotent). 재실행해도 안전하다.
"""

import asyncio
import os
import sys

from sqlalchemy import select

from src.core.asyncio_compat import configure_event_loop
from src.db.models.ip_whitelist import IpWhitelist
from src.db.session import async_engine, async_session_maker

configure_event_loop()

# 기본 전체 허용(IPv4). ⚠️ IPv6 로 붙는 클라이언트는 이 규칙에 안 걸려 403 이 되므로,
# 전면 오픈이 목적이면 IP_WHITELIST_CIDR="0.0.0.0/0,::/0" 로 실행할 것.
DEFAULT_CIDR = "0.0.0.0/0"


async def main() -> int:
    raw = os.environ.get("IP_WHITELIST_CIDR", DEFAULT_CIDR)
    cidrs = [c.strip() for c in raw.split(",") if c.strip()]
    if not cidrs:
        print("ERROR: no CIDR to seed", file=sys.stderr)
        return 1
    description = os.environ.get("IP_WHITELIST_DESC", "initial open (seed) — tighten via admin UI")

    try:
        async with async_session_maker() as session:
            for cidr in cidrs:
                existing = await session.execute(
                    select(IpWhitelist).where(IpWhitelist.ip_cidr == cidr)
                )
                if existing.scalar_one_or_none() is not None:
                    print(f"ip_whitelist already has {cidr}, skipping")
                    continue
                session.add(IpWhitelist(ip_cidr=cidr, description=description, is_active=True))
                print(f"ip_whitelist seeded: {cidr}")
            await session.commit()
        return 0
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
