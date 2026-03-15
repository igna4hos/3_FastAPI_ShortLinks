from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_and_login_roundtrip(client) -> None:
    register_response = await client.post(
        "/auth/register",
        json={"email": "User@Example.com", "password": "Password123"},
    )
    assert register_response.status_code == 201
    assert register_response.json()["email"] == "user@example.com"

    login_response = await client.post(
        "/auth/login",
        json={"email": "USER@example.com", "password": "Password123"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_register_rejects_duplicate_email(client) -> None:
    payload = {"email": "dupe@example.com", "password": "Password123"}
    first = await client.post("/auth/register", json=payload)
    second = await client.post("/auth/register", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "Email already registered"


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials(client) -> None:
    await client.post(
        "/auth/register",
        json={"email": "auth@example.com", "password": "Password123"},
    )
    response = await client.post(
        "/auth/login",
        json={"email": "auth@example.com", "password": "WrongPassword"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"
