from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_minute(dt: datetime) -> datetime:
    return ensure_utc(dt).replace(second=0, microsecond=0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
