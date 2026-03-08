from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from redis.asyncio import Redis

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.cache import invalidate_link_cache, remove_from_popularity
from app.services.link_lifecycle import cleanup_expired_and_unused_links


logger = logging.getLogger(__name__)


async def cleanup_loop(redis: Redis) -> None:
    while True:
        try:
            async with SessionLocal() as session:
                removed_codes = await cleanup_expired_and_unused_links(
                    session, settings.unused_days_threshold
                )
            for short_code in removed_codes:
                await invalidate_link_cache(redis, short_code)
                await remove_from_popularity(redis, short_code)
        except Exception:
            logger.exception("Cleanup worker iteration failed")

        await asyncio.sleep(settings.cleanup_interval_seconds)


async def stop_cleanup_task(task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
