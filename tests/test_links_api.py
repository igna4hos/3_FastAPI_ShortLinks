from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.models.link import ShortLink
from app.services.cache import POPULAR_CACHE_KEY, POPULAR_ZSET, STATS_KEY


@pytest.mark.asyncio
async def test_anonymous_link_lifecycle_search_stats_and_redirect(client, db_session) -> None:
    create_response = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/articles"},
    )
    assert create_response.status_code == 201
    payload = create_response.json()
    short_code = payload["short_code"]
    assert payload["short_url"].endswith(f"/{short_code}")

    search_response = await client.get(
        "/links/search",
        params={"original_url": "https://example.com/articles"},
    )
    assert search_response.status_code == 200
    assert search_response.json()["items"][0]["short_code"] == short_code

    stats_response = await client.get(f"/links/{short_code}/stats")
    assert stats_response.status_code == 200
    assert stats_response.json()["created_by_authenticated"] is False
    assert STATS_KEY.format(short_code) in client._transport.app.state.redis.values

    await db_session.execute(delete(ShortLink).where(ShortLink.short_code == short_code))
    await db_session.commit()

    cached_stats_response = await client.get(f"/links/{short_code}/stats")
    assert cached_stats_response.status_code == 200
    assert cached_stats_response.json()["short_code"] == short_code

    recreate_response = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/articles", "custom_alias": short_code},
    )
    assert recreate_response.status_code == 201

    redirect_response = await client.get(f"/{short_code}")
    assert redirect_response.status_code == 307
    assert redirect_response.headers["location"] == "https://example.com/articles"

    fresh_stats = await client.get(f"/links/{short_code}/stats")
    assert fresh_stats.json()["click_count"] == 1


@pytest.mark.asyncio
async def test_authenticated_crud_and_manual_archive(client, user_factory) -> None:
    user = await user_factory()
    headers = {"Authorization": f"Bearer {user['token']}"}

    create_response = await client.post(
        "/links/shorten",
        json={
            "original_url": "https://example.com/private",
            "custom_alias": "private1",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "click_limit": 5,
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    assert create_response.json()["short_code"] == "private1"

    updated = await client.put(
        "/links/private1",
        json={
            "original_url": "https://example.com/private-updated",
            "click_limit": 7,
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["original_url"] == "https://example.com/private-updated"
    assert updated.json()["click_limit"] == 7

    deleted = await client.delete("/links/private1", headers=headers)
    assert deleted.status_code == 204

    history_response = await client.get("/links/expired/history")
    assert history_response.status_code == 200
    assert history_response.json()[0]["expiration_reason"] == "manual_delete"


@pytest.mark.asyncio
async def test_update_and_delete_enforce_authentication_and_ownership(client, user_factory) -> None:
    owner = await user_factory(email="owner@example.com")
    stranger = await user_factory(email="stranger@example.com")
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    stranger_headers = {"Authorization": f"Bearer {stranger['token']}"}

    await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/owned", "custom_alias": "owned1"},
        headers=owner_headers,
    )

    unauthorized = await client.put("/links/owned1", json={"click_limit": 2})
    forbidden = await client.delete("/links/owned1", headers=stranger_headers)

    assert unauthorized.status_code == 401
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_click_limit_archives_link_after_redirect(client) -> None:
    create_response = await client.post(
        "/links/shorten",
        json={
            "original_url": "https://example.com/once",
            "custom_alias": "once123",
            "click_limit": 1,
        },
    )
    assert create_response.status_code == 201

    first_redirect = await client.get("/once123")
    second_redirect = await client.get("/once123")
    history_response = await client.get("/links/expired/history")

    assert first_redirect.status_code == 307
    assert second_redirect.status_code == 404
    assert history_response.json()[0]["expiration_reason"] == "click_limit_reached"


@pytest.mark.asyncio
async def test_popular_links_use_live_scores_and_cache(client) -> None:
    for code in ("pop1", "pop2"):
        response = await client.post(
            "/links/shorten",
            json={"original_url": f"https://example.com/{code}", "custom_alias": code},
        )
        assert response.status_code == 201

    await client.get("/pop1")
    await client.get("/pop1")
    await client.get("/pop2")

    first = await client.get("/links/popular", params={"limit": 2})
    assert first.status_code == 200
    assert first.json() == [
        {"short_code": "pop1", "total_clicks": 2},
        {"short_code": "pop2", "total_clicks": 1},
    ]
    assert POPULAR_CACHE_KEY.format(2) in client._transport.app.state.redis.values

    client._transport.app.state.redis.sorted_sets[POPULAR_ZSET].clear()
    second = await client.get("/links/popular", params={"limit": 2})
    assert second.status_code == 200
    assert second.json() == first.json()


@pytest.mark.asyncio
async def test_invalid_payloads_and_alias_conflicts(client, mocker) -> None:
    invalid_url = await client.post("/links/shorten", json={"original_url": "not-a-url"})
    invalid_alias = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com", "custom_alias": "bad alias"},
    )
    invalid_click_limit = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com", "click_limit": 0},
    )

    assert invalid_url.status_code == 422
    assert invalid_alias.status_code == 422
    assert invalid_click_limit.status_code == 422

    created = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com", "custom_alias": "taken1"},
    )
    duplicate = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/other", "custom_alias": "taken1"},
    )
    assert created.status_code == 201
    assert duplicate.status_code == 409

    mocker.patch("app.api.routes.links.generate_short_code", return_value="taken1")
    generated_failure = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/generated"},
    )
    assert generated_failure.status_code == 500


@pytest.mark.asyncio
async def test_invalid_token_falls_back_to_anonymous_and_update_validation(client, user_factory) -> None:
    invalid_token_headers = {"Authorization": "Bearer invalid-token"}
    anonymous_create = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/guest", "custom_alias": "guest01"},
        headers=invalid_token_headers,
    )
    assert anonymous_create.status_code == 201

    stats = await client.get("/links/guest01/stats")
    assert stats.status_code == 200
    assert stats.json()["created_by_authenticated"] is False

    user = await user_factory(email="validator@example.com")
    headers = {"Authorization": f"Bearer {user['token']}"}
    created = await client.post(
        "/links/shorten",
        json={"original_url": "https://example.com/validator", "custom_alias": "valid01"},
        headers=headers,
    )
    assert created.status_code == 201

    await client.get("/valid01")

    empty_update = await client.put("/links/valid01", json={}, headers=headers)
    low_limit = await client.put("/links/valid01", json={"click_limit": 1}, headers=headers)

    assert empty_update.status_code == 400
    assert low_limit.status_code == 200

    too_low_limit = await client.put("/links/valid01", json={"click_limit": 0}, headers=headers)
    assert too_low_limit.status_code == 422
