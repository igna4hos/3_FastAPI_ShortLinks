from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps as api_deps
from app.api.routes import auth as auth_routes
from app.api.routes import links as link_routes
from app.api.routes.links import normalize_expires_at
from app.core.config import Settings
from app.core.security import create_access_token, decode_access_token, get_password_hash, verify_password
from app.db.session import get_db
from app.main import create_app, lifespan
from app.models.expired_link import ExpiredLink
from app.models.link import ShortLink
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.link import ShortenLinkRequest, UpdateLinkRequest
from app.services import cleanup_worker
from app.services.cache import (
    POPULAR_CACHE_KEY,
    POPULAR_ZSET,
    REDIRECT_KEY,
    STATS_KEY,
    cache_popular,
    cache_redirect,
    cache_stats,
    get_cached_popular,
    get_cached_redirect,
    get_cached_stats,
    get_popular_codes,
    invalidate_link_cache,
    invalidate_popular_cache,
    remove_from_popularity,
)
from app.services.datetime_utils import ensure_utc, to_utc_minute
from app.services.link_lifecycle import (
    cleanup_expired_and_unused_links,
    resolve_short_code_and_track_click,
)
from app.services.shortcode import ALPHABET, generate_short_code
from tests.fakes import FakeRedis


@pytest.mark.asyncio
async def test_generate_short_code_uses_requested_length_and_alphabet() -> None:
    code = generate_short_code(24)
    assert len(code) == 24
    assert set(code) <= set(ALPHABET)


def test_normalize_expires_at_rounds_to_minute_and_rejects_past(mocker) -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    mocker.patch("app.api.routes.links.utc_now", return_value=now)

    rounded = normalize_expires_at(datetime(2026, 3, 15, 12, 2, 55, tzinfo=timezone.utc))
    assert rounded == datetime(2026, 3, 15, 12, 2, tzinfo=timezone.utc)

    with pytest.raises(HTTPException) as exc_info:
        normalize_expires_at(datetime(2026, 3, 15, 11, 59, tzinfo=timezone.utc))
    assert exc_info.value.status_code == 400


def test_datetime_helpers_normalize_naive_values() -> None:
    naive = datetime(2026, 3, 15, 12, 34, 56)
    assert ensure_utc(naive) == datetime(2026, 3, 15, 12, 34, 56, tzinfo=timezone.utc)
    assert to_utc_minute(naive) == datetime(2026, 3, 15, 12, 34, tzinfo=timezone.utc)


def test_security_roundtrip_and_invalid_token() -> None:
    hashed = get_password_hash("Password123")
    assert verify_password("Password123", hashed) is True
    assert verify_password("wrong-password", hashed) is False

    token = create_access_token("42", expires_delta=timedelta(minutes=5))
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert decode_access_token("not-a-token") is None


def test_settings_normalizes_database_url() -> None:
    settings = Settings(database_url="postgres://user:pass@localhost:5432/app")
    assert settings.database_url == "postgresql+asyncpg://user:pass@localhost:5432/app"


@pytest.mark.asyncio
async def test_cache_helpers_decode_and_invalidate(fake_redis: FakeRedis) -> None:
    await cache_redirect(fake_redis, "abc", "https://example.com")
    await cache_stats(fake_redis, "abc", {"click_count": 2})
    await cache_popular(fake_redis, 3, [{"short_code": "abc", "total_clicks": 2}])
    await fake_redis.zincrby(POPULAR_ZSET, 2, "abc")
    await fake_redis.zincrby(POPULAR_ZSET, 5, "xyz")

    fake_redis.values[REDIRECT_KEY.format("bytes")] = b"https://bytes.example"
    fake_redis.values[STATS_KEY.format("bytes")] = b'{"click_count": 7}'

    assert await get_cached_redirect(fake_redis, "abc") == "https://example.com"
    assert await get_cached_redirect(fake_redis, "bytes") == "https://bytes.example"
    assert await get_cached_stats(fake_redis, "abc") == {"click_count": 2}
    assert await get_cached_stats(fake_redis, "bytes") == {"click_count": 7}
    assert await get_cached_popular(fake_redis, 3) == [{"short_code": "abc", "total_clicks": 2}]
    assert await get_popular_codes(fake_redis, 2) == [("xyz", 5.0), ("abc", 2.0)]

    await invalidate_link_cache(fake_redis, "abc")
    assert await get_cached_redirect(fake_redis, "abc") is None
    assert await get_cached_stats(fake_redis, "abc") is None

    await invalidate_popular_cache(fake_redis)
    assert await get_cached_popular(fake_redis, 3) is None

    await remove_from_popularity(fake_redis, "xyz")
    assert await get_popular_codes(fake_redis, 2) == [("abc", 2.0)]


@pytest.mark.asyncio
async def test_dependency_helpers_resolve_users_and_redis(db_session, fake_redis: FakeRedis) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=fake_redis)))
    assert await api_deps.get_redis(request) is fake_redis

    assert await api_deps.get_current_user_optional(None, db_session) is None

    invalid_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token")
    assert await api_deps.get_current_user_optional(invalid_creds, db_session) is None

    bad_sub_token = create_access_token("not-an-int")
    bad_sub_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bad_sub_token)
    assert await api_deps.get_current_user_optional(bad_sub_creds, db_session) is None

    missing_user_token = create_access_token("999")
    missing_user_creds = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=missing_user_token,
    )
    assert await api_deps.get_current_user_optional(missing_user_creds, db_session) is None

    user = User(email="deps@example.com", hashed_password=get_password_hash("Password123"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    valid_creds = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_access_token(str(user.id)),
    )
    current_user = await api_deps.get_current_user_optional(valid_creds, db_session)
    assert current_user.id == user.id
    assert await api_deps.get_current_user(current_user) is current_user

    with pytest.raises(HTTPException) as exc_info:
        await api_deps.get_current_user(None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_routes_directly(db_session) -> None:
    register_payload = RegisterRequest(email="RouteUser@example.com", password="Password123")
    user = await auth_routes.register(register_payload, db_session)
    assert user.email == "routeuser@example.com"

    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.register(register_payload, db_session)
    assert exc_info.value.status_code == 409

    token_response = await auth_routes.login(
        LoginRequest(email="routeuser@example.com", password="Password123"),
        db_session,
    )
    assert token_response.access_token

    with pytest.raises(HTTPException) as invalid_login:
        await auth_routes.login(
            LoginRequest(email="routeuser@example.com", password="wrong-password"),
            db_session,
        )
    assert invalid_login.value.status_code == 401


@pytest.mark.asyncio
async def test_get_db_yields_async_session(session_factory) -> None:
    session_generator = get_db.__wrapped__() if hasattr(get_db, "__wrapped__") else get_db()
    session = await session_generator.__anext__()
    assert isinstance(session, AsyncSession)
    await session_generator.aclose()


def test_schema_validators_accept_expected_values() -> None:
    shorten_request = ShortenLinkRequest(
        original_url="https://example.com",
        custom_alias="valid_alias-01",
    )
    update_request = UpdateLinkRequest(expires_at=datetime(2026, 3, 20, 10, 30, tzinfo=timezone.utc))

    assert shorten_request.custom_alias == "valid_alias-01"
    assert update_request.expires_at == datetime(2026, 3, 20, 10, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_link_routes_directly_cover_crud_paths(db_session, fake_redis: FakeRedis) -> None:
    user = User(email="links@example.com", hashed_password=get_password_hash("Password123"))
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    created = await link_routes.shorten_link(
        ShortenLinkRequest(
            original_url="https://example.com/direct",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=1),
        ),
        db_session,
        fake_redis,
        user,
    )
    assert created.short_code

    duplicate_payload = ShortenLinkRequest(
        original_url="https://example.com/direct-duplicate",
        custom_alias="manual1",
    )
    await link_routes.shorten_link(duplicate_payload, db_session, fake_redis, user)
    with pytest.raises(HTTPException) as duplicate_exc:
        await link_routes.shorten_link(duplicate_payload, db_session, fake_redis, user)
    assert duplicate_exc.value.status_code == 409

    search = await link_routes.search_by_original_url("https://example.com/direct", db_session)
    assert search.items[0].original_url == "https://example.com/direct"

    stats = await link_routes.link_stats(created.short_code, db_session, fake_redis)
    assert stats.created_by_authenticated is True

    link = await link_routes.get_link_or_404(db_session, created.short_code)
    assert link.short_code == created.short_code
    link_routes.enforce_owner(link, user)

    updated = await link_routes.update_link(
        created.short_code,
        UpdateLinkRequest(original_url="https://example.com/updated", click_limit=3),
        db_session,
        fake_redis,
        user,
    )
    assert updated.original_url == "https://example.com/updated"
    assert updated.click_limit == 3

    with pytest.raises(HTTPException) as no_fields:
        await link_routes.update_link(
            created.short_code,
            UpdateLinkRequest(),
            db_session,
            fake_redis,
            user,
        )
    assert no_fields.value.status_code == 400

    link.click_count = 2
    await db_session.commit()
    with pytest.raises(HTTPException) as low_limit:
        await link_routes.update_link(
            created.short_code,
            UpdateLinkRequest(click_limit=1),
            db_session,
            fake_redis,
            user,
        )
    assert low_limit.value.status_code == 400

    response = await link_routes.delete_link(created.short_code, db_session, fake_redis, user)
    assert response.status_code == 204

    history = await link_routes.expired_history(limit=10, db=db_session)
    assert history[0].expiration_reason == "manual_delete"


@pytest.mark.asyncio
async def test_link_routes_directly_cover_popular_and_redirect_paths(db_session, fake_redis: FakeRedis) -> None:
    popular_link = ShortLink(
        short_code="popular1",
        original_url="https://example.com/popular",
        click_count=4,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(popular_link)
    await db_session.commit()

    popular_from_db = await link_routes.popular_links(limit=5, redis=fake_redis, db=db_session)
    assert popular_from_db == [link_routes.PopularLinkResponse(short_code="popular1", total_clicks=4)]

    cached_popular = await link_routes.popular_links(limit=5, redis=fake_redis, db=db_session)
    assert cached_popular == popular_from_db

    active_link = ShortLink(
        short_code="redir1",
        original_url="https://example.com/redirect",
        updated_at=datetime.now(timezone.utc),
    )
    limit_link = ShortLink(
        short_code="redir2",
        original_url="https://example.com/final",
        click_limit=1,
        updated_at=datetime.now(timezone.utc),
    )
    expired_link = ShortLink(
        short_code="expired1",
        original_url="https://example.com/expired",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([active_link, limit_link, expired_link])
    await db_session.commit()

    redirect_response = await link_routes.execute_redirect("redir1", db_session, fake_redis)
    assert redirect_response.status_code == 307
    assert await get_cached_redirect(fake_redis, "redir1") == "https://example.com/redirect"
    assert await get_popular_codes(fake_redis, 5) == [("redir1", 1.0)]

    limit_response = await link_routes.execute_redirect("redir2", db_session, fake_redis)
    assert limit_response.status_code == 307

    with pytest.raises(HTTPException) as missing_redirect:
        await link_routes.execute_redirect("missing", db_session, fake_redis)
    assert missing_redirect.value.status_code == 404

    with pytest.raises(HTTPException) as expired_exc:
        await link_routes.get_link_or_404(db_session, "expired1")
    assert expired_exc.value.status_code == 404

    wrapper_response = await link_routes.redirect_from_links_prefix("redir1", db_session, fake_redis)
    assert wrapper_response.status_code == 307


@pytest.mark.asyncio
async def test_shorten_link_rolls_back_on_integrity_error(mocker, fake_redis: FakeRedis) -> None:
    db = SimpleNamespace(
        execute=AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))),
        commit=AsyncMock(side_effect=IntegrityError("insert", {}, None)),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
        add=MagicMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await link_routes.shorten_link(
            ShortenLinkRequest(original_url="https://example.com/integrity", custom_alias="integrity1"),
            db,
            fake_redis,
            None,
        )

    assert exc_info.value.status_code == 409
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_short_code_tracks_click_and_archives_limit_reached(db_session) -> None:
    link = ShortLink(
        short_code="limit1",
        original_url="https://example.com",
        click_limit=1,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(link)
    await db_session.commit()

    resolved_url, still_active = await resolve_short_code_and_track_click(db_session, "limit1")
    assert resolved_url == "https://example.com"
    assert still_active is False

    result = await db_session.execute(select(ShortLink).where(ShortLink.short_code == "limit1"))
    assert result.scalar_one_or_none() is None

    archived = await db_session.execute(
        select(ExpiredLink).where(ExpiredLink.short_code == "limit1")
    )
    archived_link = archived.scalar_one()
    assert archived_link.expiration_reason == "click_limit_reached"
    assert archived_link.click_count == 1


@pytest.mark.asyncio
async def test_resolve_short_code_handles_missing_expired_and_active_links(db_session) -> None:
    missing = await resolve_short_code_and_track_click(db_session, "missing")
    assert missing == (None, False)

    expired = ShortLink(
        short_code="expired2",
        original_url="https://example.com/expired",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        updated_at=datetime.now(timezone.utc),
    )
    active = ShortLink(
        short_code="active1",
        original_url="https://example.com/active",
        updated_at=datetime.now(timezone.utc),
    )
    already_limited = ShortLink(
        short_code="limited2",
        original_url="https://example.com/limited",
        click_limit=1,
        click_count=1,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add_all([expired, active, already_limited])
    await db_session.commit()

    expired_result = await resolve_short_code_and_track_click(db_session, "expired2")
    assert expired_result == (None, False)

    limited_result = await resolve_short_code_and_track_click(db_session, "limited2")
    assert limited_result == (None, False)

    active_result = await resolve_short_code_and_track_click(db_session, "active1")
    assert active_result == ("https://example.com/active", True)


@pytest.mark.asyncio
async def test_cleanup_expired_and_unused_links_collects_all_reasons(db_session) -> None:
    now = datetime.now(timezone.utc)
    links = [
        ShortLink(
            short_code="expired",
            original_url="https://expired.example",
            expires_at=now - timedelta(minutes=1),
            updated_at=now,
        ),
        ShortLink(
            short_code="limited",
            original_url="https://limited.example",
            click_limit=2,
            click_count=2,
            updated_at=now,
        ),
        ShortLink(
            short_code="unused",
            original_url="https://unused.example",
            created_at=now - timedelta(days=40),
            updated_at=now - timedelta(days=40),
        ),
        ShortLink(
            short_code="fresh",
            original_url="https://fresh.example",
            created_at=now,
            updated_at=now,
        ),
    ]
    db_session.add_all(links)
    await db_session.commit()

    removed_codes = await cleanup_expired_and_unused_links(db_session, unused_days_threshold=30)
    assert sorted(removed_codes) == ["expired", "limited", "unused"]

    archived = await db_session.execute(select(ExpiredLink))
    reasons = {item.short_code: item.expiration_reason for item in archived.scalars()}
    assert reasons == {
        "expired": "expired_at",
        "limited": "click_limit_reached",
        "unused": "unused_timeout",
    }


@pytest.mark.asyncio
async def test_cleanup_loop_invalidates_removed_codes(mocker) -> None:
    fake_redis = FakeRedis()
    invalidate_mock = AsyncMock()
    remove_mock = AsyncMock()

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_cleanup(session, threshold):
        assert threshold > 0
        assert session is not None
        return ["alpha", "beta"]

    async def fake_sleep(seconds):
        assert seconds >= 0
        raise asyncio.CancelledError

    mocker.patch.object(cleanup_worker, "SessionLocal", return_value=fake_session_scope())
    mocker.patch.object(cleanup_worker, "cleanup_expired_and_unused_links", side_effect=fake_cleanup)
    mocker.patch.object(cleanup_worker, "invalidate_link_cache", invalidate_mock)
    mocker.patch.object(cleanup_worker, "remove_from_popularity", remove_mock)
    mocker.patch.object(cleanup_worker.asyncio, "sleep", side_effect=fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await cleanup_worker.cleanup_loop(fake_redis)

    assert invalidate_mock.await_args_list == [
        call(fake_redis, "alpha"),
        call(fake_redis, "beta"),
    ]
    assert remove_mock.await_args_list == [
        call(fake_redis, "alpha"),
        call(fake_redis, "beta"),
    ]


@pytest.mark.asyncio
async def test_cleanup_loop_logs_and_continues_after_exceptions(mocker) -> None:
    fake_redis = FakeRedis()
    logger_mock = mocker.patch.object(cleanup_worker, "logger")

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    async def fake_sleep(seconds):
        assert seconds >= 0
        raise asyncio.CancelledError

    mocker.patch.object(cleanup_worker, "SessionLocal", return_value=fake_session_scope())
    mocker.patch.object(
        cleanup_worker,
        "cleanup_expired_and_unused_links",
        side_effect=RuntimeError("boom"),
    )
    mocker.patch.object(cleanup_worker.asyncio, "sleep", side_effect=fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await cleanup_worker.cleanup_loop(fake_redis)

    logger_mock.exception.assert_called_once()


@pytest.mark.asyncio
async def test_stop_cleanup_task_handles_none_and_cancelled_task() -> None:
    await cleanup_worker.stop_cleanup_task(None)

    async def never_finishes():
        await asyncio.sleep(3600)

    task = asyncio.create_task(never_finishes())
    await cleanup_worker.stop_cleanup_task(task)
    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_lifespan_initializes_and_cleans_resources(mocker) -> None:
    fake_conn = AsyncMock()

    @asynccontextmanager
    async def fake_begin():
        yield fake_conn

    fake_redis = AsyncMock()
    fake_cleanup_task = object()
    fake_app = SimpleNamespace(state=SimpleNamespace())

    async def fake_cleanup_loop(redis):
        assert redis is fake_redis

    def fake_create_task(coro):
        coro.close()
        return fake_cleanup_task

    stop_cleanup_task = AsyncMock()
    fake_engine = SimpleNamespace(begin=fake_begin, dispose=AsyncMock())
    mocker.patch("app.main.engine", fake_engine)
    mocker.patch("app.main.Redis.from_url", return_value=fake_redis)
    mocker.patch("app.main.cleanup_loop", side_effect=fake_cleanup_loop)
    mocker.patch("app.main.asyncio.create_task", side_effect=fake_create_task)
    mocker.patch("app.main.stop_cleanup_task", stop_cleanup_task)

    async with lifespan(fake_app):
        assert fake_app.state.redis is fake_redis
        assert fake_app.state.cleanup_task is fake_cleanup_task

    fake_conn.run_sync.assert_awaited_once()
    fake_redis.ping.assert_awaited_once()
    stop_cleanup_task.assert_awaited_once_with(fake_cleanup_task)
    fake_redis.close.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


def test_create_app_can_skip_lifespan() -> None:
    app = create_app(with_lifespan=False)
    assert app.title
