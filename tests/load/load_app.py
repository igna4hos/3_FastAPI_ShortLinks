from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.api.deps import get_redis
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from tests.fakes import FakeRedis


DB_PATH = ROOT_DIR / "tests" / "load" / "loadtest.sqlite3"
if DB_PATH.exists():
    DB_PATH.unlink()

engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
redis = FakeRedis()


async def prepare_database() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

app = create_app(with_lifespan=False)
app.state.redis = redis


async def override_get_db():
    async with SessionLocal() as session:
        yield session


async def override_get_redis():
    return redis


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_redis] = override_get_redis


@app.on_event("startup")
async def startup() -> None:
    await prepare_database()


@app.on_event("shutdown")
async def shutdown() -> None:
    await engine.dispose()
