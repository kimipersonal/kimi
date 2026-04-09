"""Database connection and session management."""

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from app.config import get_settings

settings = get_settings()

# --- PostgreSQL ---
engine = create_async_engine(
    settings.database_url, echo=settings.debug, pool_size=10, max_overflow=20
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# --- Redis ---
redis_pool: aioredis.Redis = aioredis.from_url(
    settings.redis_url, decode_responses=True, max_connections=20
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def get_redis() -> aioredis.Redis:
    """Return the shared Redis connection pool."""
    return redis_pool


async def init_db():
    async with engine.begin() as conn:
        # Enable pgvector extension before creating tables
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if settings.debug:
            await conn.run_sync(Base.metadata.create_all)
