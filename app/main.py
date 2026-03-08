import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from redis.asyncio import Redis

from app.api.deps import get_redis
from app.api.routes import auth, links
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine, get_db
from app.services.cleanup_worker import cleanup_loop, stop_cleanup_task


logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    redis = Redis.from_url(settings.redis_url)
    await redis.ping()

    app.state.redis = redis
    app.state.cleanup_task = asyncio.create_task(cleanup_loop(redis))

    try:
        yield
    finally:
        await stop_cleanup_task(app.state.cleanup_task)
        await redis.close()
        await engine.dispose()


app = FastAPI(title=settings.project_name, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(links.router)


@app.get("/{short_code}")
async def redirect_from_root(
    short_code: str,
    db=Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    return await links.execute_redirect(short_code=short_code, db=db, redis=redis)
