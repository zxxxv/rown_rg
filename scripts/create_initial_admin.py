"""Create the initial super_admin user from env vars.

Usage:
    INITIAL_ADMIN_EMAIL=... INITIAL_ADMIN_PASSWORD=... \
        uv run python scripts/create_initial_admin.py

Skips if any super_admin already exists.
"""

import asyncio
import os
import sys

from sqlalchemy import select

from src.core.asyncio_compat import configure_event_loop
from src.db.models.user import User
from src.db.session import async_engine, async_session_maker
from src.infrastructure.auth.password_handler import hash_password, validate_password_policy

configure_event_loop()


async def main() -> int:
    email = os.environ.get("INITIAL_ADMIN_EMAIL")
    password = os.environ.get("INITIAL_ADMIN_PASSWORD")
    name = os.environ.get("INITIAL_ADMIN_NAME", "Initial Admin")
    if not email or not password:
        print(
            "ERROR: INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD must be set",
            file=sys.stderr,
        )
        return 1

    try:
        validate_password_policy(password)

        async with async_session_maker() as session:
            existing = await session.execute(select(User).where(User.role == "super_admin"))
            if existing.scalar_one_or_none() is not None:
                print("super_admin already exists, skipping")
                return 0

            user = User(
                email=email,
                name=name,
                role="super_admin",
                password_hash=hash_password(password),
                is_active=True,
            )
            session.add(user)
            await session.commit()
            print(f"super_admin created: {email}")
            return 0
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
