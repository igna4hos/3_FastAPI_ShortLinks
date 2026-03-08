from datetime import datetime, timezone


def to_utc_minute(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
