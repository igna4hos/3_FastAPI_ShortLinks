from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expired_link import ExpiredLink
from app.models.link import ShortLink
from app.services.datetime_utils import ensure_utc, utc_now


async def archive_and_delete_link(session: AsyncSession, link: ShortLink, reason: str) -> None:
    expired = ExpiredLink(
        short_code=link.short_code,
        original_url=link.original_url,
        created_at=link.created_at,
        expired_at=utc_now(),
        last_used_at=link.last_used_at,
        click_count=link.click_count,
        click_limit=link.click_limit,
        creator_user_id=link.creator_user_id,
        expiration_reason=reason,
    )
    session.add(expired)
    await session.delete(link)


async def resolve_short_code_and_track_click(
    session: AsyncSession,
    short_code: str,
) -> tuple[Optional[str], bool]:
    stmt = select(ShortLink).where(
        and_(ShortLink.short_code == short_code, ShortLink.is_active.is_(True))
    )
    result = await session.execute(stmt)
    link = result.scalar_one_or_none()
    if link is None:
        return None, False

    now = utc_now()
    expires_at = ensure_utc(link.expires_at)

    if expires_at and expires_at <= now:
        await archive_and_delete_link(session, link, reason="expired_at")
        await session.commit()
        return None, False

    if link.click_limit is not None and link.click_count >= link.click_limit:
        await archive_and_delete_link(session, link, reason="click_limit_reached")
        await session.commit()
        return None, False

    original_url = link.original_url
    link.click_count += 1
    link.last_used_at = now
    link.updated_at = now

    if link.click_limit is not None and link.click_count >= link.click_limit:
        await archive_and_delete_link(session, link, reason="click_limit_reached")
        await session.commit()
        return original_url, False

    await session.commit()
    return original_url, True


async def cleanup_expired_and_unused_links(
    session: AsyncSession,
    unused_days_threshold: int,
) -> list[str]:
    now = utc_now()
    cutoff = now - timedelta(days=unused_days_threshold)

    stmt = select(ShortLink).where(ShortLink.is_active.is_(True))
    result = await session.execute(stmt)
    links = result.scalars().all()

    removed_codes: list[str] = []
    for link in links:
        expires_at = ensure_utc(link.expires_at)
        last_used_at = ensure_utc(link.last_used_at)
        created_at = ensure_utc(link.created_at)

        should_remove = (
            (expires_at is not None and expires_at <= now)
            or (link.click_limit is not None and link.click_count >= link.click_limit)
            or (last_used_at is not None and last_used_at <= cutoff)
            or (last_used_at is None and created_at is not None and created_at <= cutoff)
        )
        if not should_remove:
            continue

        if expires_at and expires_at <= now:
            reason = "expired_at"
        elif link.click_limit is not None and link.click_count >= link.click_limit:
            reason = "click_limit_reached"
        else:
            reason = "unused_timeout"

        removed_codes.append(link.short_code)
        await archive_and_delete_link(session, link, reason)

    if links:
        await session.commit()

    return removed_codes
