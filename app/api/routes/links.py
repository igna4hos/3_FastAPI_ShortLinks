from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_optional, get_redis
from app.core.config import settings
from app.db.session import get_db
from app.models.expired_link import ExpiredLink
from app.models.link import ShortLink
from app.models.user import User
from app.schemas.link import (
    ExpiredLinkResponse,
    LinkSearchItem,
    LinkStatsResponse,
    PopularLinkResponse,
    SearchResponse,
    ShortLinkResponse,
    ShortenLinkRequest,
    UpdateLinkRequest,
)
from app.services.cache import (
    bump_popularity,
    cache_popular,
    cache_redirect,
    cache_stats,
    get_cached_popular,
    get_cached_redirect,
    get_cached_stats,
    get_popular_codes,
    invalidate_link_cache,
    remove_from_popularity,
)
from app.services.datetime_utils import ensure_utc, to_utc_minute, utc_now
from app.services.link_lifecycle import archive_and_delete_link, resolve_short_code_and_track_click
from app.services.shortcode import generate_short_code


router = APIRouter(prefix="/links", tags=["links"])


def build_short_url(short_code: str) -> str:
    return f"{settings.base_url.rstrip('/')}/{short_code}"


def normalize_expires_at(expires_at: Optional[datetime]) -> Optional[datetime]:
    if expires_at is None:
        return None

    normalized = to_utc_minute(expires_at)
    if normalized <= utc_now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expires_at must be in the future",
        )
    return normalized


async def get_link_or_404(db: AsyncSession, short_code: str) -> ShortLink:
    result = await db.execute(
        select(ShortLink).where(ShortLink.short_code == short_code, ShortLink.is_active.is_(True))
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    now = utc_now()
    expires_at = ensure_utc(link.expires_at)
    if (expires_at and expires_at <= now) or (
        link.click_limit is not None and link.click_count >= link.click_limit
    ):
        reason = "expired_at" if expires_at and expires_at <= now else "click_limit_reached"
        await archive_and_delete_link(db, link, reason=reason)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    return link


def enforce_owner(link: ShortLink, user: User) -> None:
    if link.creator_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage links created by your account",
        )


@router.post("/shorten", response_model=ShortLinkResponse, status_code=status.HTTP_201_CREATED)
async def shorten_link(
    payload: ShortenLinkRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> ShortLinkResponse:
    short_code = payload.custom_alias
    expires_at = normalize_expires_at(payload.expires_at)

    if short_code:
        existing = await db.execute(select(ShortLink.id).where(ShortLink.short_code == short_code))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="custom_alias already exists",
            )
    else:
        for _ in range(10):
            candidate = generate_short_code(settings.shortcode_length)
            exists = await db.execute(select(ShortLink.id).where(ShortLink.short_code == candidate))
            if exists.scalar_one_or_none() is None:
                short_code = candidate
                break
        if not short_code:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not generate unique short code",
            )

    link = ShortLink(
        short_code=short_code,
        original_url=str(payload.original_url),
        expires_at=expires_at,
        click_limit=payload.click_limit,
        creator_user_id=current_user.id if current_user else None,
        created_by_authenticated=current_user is not None,
        updated_at=utc_now(),
    )

    db.add(link)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Short code already exists")

    await db.refresh(link)
    await cache_redirect(redis, link.short_code, link.original_url)

    return ShortLinkResponse(
        short_code=link.short_code,
        short_url=build_short_url(link.short_code),
        original_url=link.original_url,
        created_at=ensure_utc(link.created_at),
        expires_at=ensure_utc(link.expires_at),
        click_limit=link.click_limit,
    )


@router.get("/search", response_model=SearchResponse)
async def search_by_original_url(
    original_url: str = Query(..., min_length=5),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    result = await db.execute(
        select(ShortLink)
        .where(ShortLink.original_url == original_url, ShortLink.is_active.is_(True))
        .order_by(desc(ShortLink.created_at))
    )
    links = result.scalars().all()

    items = [
        LinkSearchItem(
            short_code=link.short_code,
            short_url=build_short_url(link.short_code),
            original_url=link.original_url,
            created_at=ensure_utc(link.created_at),
            expires_at=ensure_utc(link.expires_at),
        )
        for link in links
    ]
    return SearchResponse(items=items)


@router.get("/expired/history", response_model=list[ExpiredLinkResponse])
async def expired_history(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[ExpiredLinkResponse]:
    result = await db.execute(select(ExpiredLink).order_by(desc(ExpiredLink.expired_at)).limit(limit))
    links = result.scalars().all()
    return [
        ExpiredLinkResponse(
            short_code=item.short_code,
            original_url=item.original_url,
            created_at=ensure_utc(item.created_at),
            expired_at=ensure_utc(item.expired_at),
            last_used_at=ensure_utc(item.last_used_at),
            click_count=item.click_count,
            click_limit=item.click_limit,
            expiration_reason=item.expiration_reason,
        )
        for item in links
    ]


@router.get("/popular", response_model=list[PopularLinkResponse])
async def popular_links(
    limit: int = Query(default=10, ge=1, le=100),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> list[PopularLinkResponse]:
    cached = await get_cached_popular(redis, limit)
    if cached is not None:
        return [PopularLinkResponse(**item) for item in cached]

    scored_codes = await get_popular_codes(redis, limit)
    if not scored_codes:
        result = await db.execute(
            select(ShortLink.short_code, ShortLink.click_count)
            .where(ShortLink.is_active.is_(True))
            .order_by(desc(ShortLink.click_count))
            .limit(limit)
        )
        items = [
            PopularLinkResponse(short_code=code, total_clicks=clicks)
            for code, clicks in result.all()
            if clicks > 0
        ]
    else:
        items = [
            PopularLinkResponse(short_code=code, total_clicks=int(score))
            for code, score in scored_codes
        ]

    serialized = [item.model_dump() for item in items]
    await cache_popular(redis, limit, serialized)
    return items


@router.get("/{short_code}/stats", response_model=LinkStatsResponse)
async def link_stats(
    short_code: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> LinkStatsResponse:
    cached = await get_cached_stats(redis, short_code)
    if cached is not None:
        return LinkStatsResponse(**cached)

    link = await get_link_or_404(db, short_code)

    payload = {
        "short_code": link.short_code,
        "original_url": link.original_url,
        "created_at": ensure_utc(link.created_at).isoformat(),
        "click_count": link.click_count,
        "last_used_at": ensure_utc(link.last_used_at).isoformat() if link.last_used_at else None,
        "expires_at": ensure_utc(link.expires_at).isoformat() if link.expires_at else None,
        "click_limit": link.click_limit,
        "created_by_authenticated": link.created_by_authenticated,
    }
    await cache_stats(redis, short_code, payload)
    return LinkStatsResponse(**payload)


@router.put("/{short_code}", response_model=ShortLinkResponse)
async def update_link(
    short_code: str,
    payload: UpdateLinkRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> ShortLinkResponse:
    link = await get_link_or_404(db, short_code)
    enforce_owner(link, current_user)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    if "original_url" in updates:
        link.original_url = str(payload.original_url)

    if "expires_at" in updates:
        link.expires_at = normalize_expires_at(payload.expires_at)

    if "click_limit" in updates:
        if payload.click_limit is not None and payload.click_limit < link.click_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="click_limit cannot be lower than current click_count",
            )
        link.click_limit = payload.click_limit

    link.updated_at = utc_now()

    await db.commit()
    await db.refresh(link)

    await invalidate_link_cache(redis, short_code)
    await cache_redirect(redis, short_code, link.original_url)

    return ShortLinkResponse(
        short_code=link.short_code,
        short_url=build_short_url(link.short_code),
        original_url=link.original_url,
        created_at=ensure_utc(link.created_at),
        expires_at=ensure_utc(link.expires_at),
        click_limit=link.click_limit,
    )


@router.delete("/{short_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    short_code: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> Response:
    link = await get_link_or_404(db, short_code)
    enforce_owner(link, current_user)

    await archive_and_delete_link(db, link, reason="manual_delete")
    await db.commit()

    await invalidate_link_cache(redis, short_code)
    await remove_from_popularity(redis, short_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def execute_redirect(
    short_code: str,
    db: AsyncSession,
    redis: Redis,
) -> RedirectResponse:
    cached_url = await get_cached_redirect(redis, short_code)
    resolved_url, still_active = await resolve_short_code_and_track_click(db, short_code)

    if resolved_url is None:
        await invalidate_link_cache(redis, short_code)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    await invalidate_link_cache(redis, short_code)
    if still_active and cached_url is None:
        await cache_redirect(redis, short_code, resolved_url)
    if still_active:
        await bump_popularity(redis, short_code)
    else:
        await remove_from_popularity(redis, short_code)

    return RedirectResponse(url=resolved_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/{short_code}")
async def redirect_from_links_prefix(
    short_code: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    return await execute_redirect(short_code=short_code, db=db, redis=redis)
