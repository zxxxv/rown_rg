"""Windows asyncio 호환성 처리 (psycopg 등 SelectorEventLoop 의존 라이브러리)"""

import asyncio
import sys


def configure_event_loop() -> None:
    """Windows에서 SelectorEventLoop 강제. psycopg async 호환 위함."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
