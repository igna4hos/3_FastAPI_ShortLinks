from __future__ import annotations

from fnmatch import fnmatch


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def setex(self, key: str, ttl: int, value: str | bytes) -> None:
        del ttl
        self.values[key] = value

    async def get(self, key: str) -> str | bytes | None:
        return self.values.get(key)

    async def zincrby(self, name: str, increment: int, member: str) -> float:
        bucket = self.sorted_sets.setdefault(name, {})
        bucket[member] = bucket.get(member, 0.0) + increment
        return bucket[member]

    async def zrevrange(
        self,
        name: str,
        start: int,
        end: int,
        withscores: bool = False,
    ) -> list[str] | list[tuple[str, float]]:
        bucket = self.sorted_sets.get(name, {})
        rows = sorted(bucket.items(), key=lambda item: (-item[1], item[0]))
        if end == -1:
            selected = rows[start:]
        else:
            selected = rows[start : end + 1]
        if withscores:
            return [(member, score) for member, score in selected]
        return [member for member, _ in selected]

    async def zrem(self, name: str, member: str) -> int:
        bucket = self.sorted_sets.get(name, {})
        existed = member in bucket
        bucket.pop(member, None)
        return int(existed)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
            if key in self.sorted_sets:
                del self.sorted_sets[key]
                deleted += 1
        return deleted

    async def scan_iter(self, match: str):
        for key in list(self.values):
            if fnmatch(key, match):
                yield key
