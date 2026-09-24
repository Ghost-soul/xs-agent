"""Refuse a tracked restart while calls, generation batches or local tasks are active."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from novel_writer.core.config import get_settings
from novel_writer.core.maintenance import ACTIVE_WORK_QUERY
from novel_writer.db.engine import Database


async def _main() -> int:
    database = Database(get_settings().database_url)
    try:
        async with database.session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            active_work = await session.scalar(text(ACTIVE_WORK_QUERY))
    finally:
        await database.dispose()
    if active_work == 0:
        return 0
    print(
        "Refusing restart: calls, generation batches or local tasks are active. "
        "Wait for completion or pause before restarting the tracked backend."
    )
    return 2


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(_main()))
