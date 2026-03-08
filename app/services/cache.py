from __future__ import annotations

import json
from typing import Optional

from redis.asyncio import Redis

from app.core.config import settings


REDIRECT_KEY = "link:redirect:{}"
STATS_KEY = "link:stats:{}"
POPULAR_ZSET = "links:popular"
POPULAR_CACHE_KEY = "links:popular:top:{}"


async def cache_redirect(redis: Redis, short_code: str, url: str) -> None:
    await redis.setex(REDIRECT_KEY.format(short_code), settings.redirect_cache_ttl_seconds, url)


async def get_cached_redirect(redis: Redis, short_code: str) -> Optional[str]:
    value = await redis.get(REDIRECT_KEY.format(short_code))
    return value.decode() if isinstance(value, bytes) else value


async def cache_stats(redis: Redis, short_code: str, payload: dict) -> None:
    await redis.setex(
        STATS_KEY.format(short_code),
        settings.stats_cache_ttl_seconds,
        json.dumps(payload, default=str),
    )


async def get_cached_stats(redis: Redis, short_code: str) -> Optional[dict]:
    value = await redis.get(STATS_KEY.format(short_code))
    if not value:
        return None
    raw = value.decode() if isinstance(value, bytes) else value
    return json.loads(raw)


async def bump_popularity(redis: Redis, short_code: str, increment: int = 1) -> None:
    await redis.zincrby(POPULAR_ZSET, increment, short_code)


async def cache_popular(redis: Redis, limit: int, payload: list[dict]) -> None:
    await redis.setex(
        POPULAR_CACHE_KEY.format(limit),
        settings.popular_cache_ttl_seconds,
        json.dumps(payload),
    )


async def get_cached_popular(redis: Redis, limit: int) -> Optional[list[dict]]:
    value = await redis.get(POPULAR_CACHE_KEY.format(limit))
    if not value:
        return None
    raw = value.decode() if isinstance(value, bytes) else value
    return json.loads(raw)


async def get_popular_codes(redis: Redis, limit: int) -> list[tuple[str, float]]:
    rows = await redis.zrevrange(POPULAR_ZSET, 0, limit - 1, withscores=True)
    result: list[tuple[str, float]] = []
    for code, score in rows:
        result.append(((code.decode() if isinstance(code, bytes) else code), float(score)))
    return result


async def invalidate_popular_cache(redis: Redis) -> None:
    keys: list[str] = []
    async for key in redis.scan_iter(match=POPULAR_CACHE_KEY.format("*")):
        keys.append(key.decode() if isinstance(key, bytes) else key)
    if keys:
        await redis.delete(*keys)


async def remove_from_popularity(redis: Redis, short_code: str) -> None:
    await redis.zrem(POPULAR_ZSET, short_code)
    await invalidate_popular_cache(redis)


async def invalidate_link_cache(redis: Redis, short_code: str) -> None:
    await redis.delete(REDIRECT_KEY.format(short_code), STATS_KEY.format(short_code))
