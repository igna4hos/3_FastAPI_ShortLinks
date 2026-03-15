from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncGenerator, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.api.deps import get_redis
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from tests.fakes import FakeRedis


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("data") / "test.db"


@pytest_asyncio.fixture(scope="session")
async def test_engine(test_db_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db_path}",
        future=True,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_engine) -> async_sessionmaker[AsyncSession]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(session_factory, fake_redis: FakeRedis):
    test_app = create_app(with_lifespan=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    async def override_get_redis() -> FakeRedis:
        return fake_redis

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_redis] = override_get_redis
    test_app.state.redis = fake_redis
    return test_app


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as async_client:
        yield async_client


@pytest_asyncio.fixture
async def user_factory(client: AsyncClient) -> Callable[..., dict]:
    counter = 0

    async def create_user(email: str | None = None, password: str = "Password123") -> dict:
        nonlocal counter
        counter += 1
        selected_email = email or f"user{counter}@example.com"
        register_response = await client.post(
            "/auth/register",
            json={"email": selected_email, "password": password},
        )
        assert register_response.status_code == 201, register_response.text

        login_response = await client.post(
            "/auth/login",
            json={"email": selected_email, "password": password},
        )
        assert login_response.status_code == 200, login_response.text

        return {
            "id": register_response.json()["id"],
            "email": selected_email,
            "password": password,
            "token": login_response.json()["access_token"],
        }

    return create_user
