"""Engine / session setup (local Postgres or Cloud SQL unix socket)."""

import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from gcpbasicdeployment.core.config import get_settings
from gcpbasicdeployment.db.models import Base

logger = logging.getLogger(__name__)


@lru_cache
def get_engine() -> AsyncEngine:
    """Create the engine lazily; no connection is opened until first use."""
    return create_async_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with get_sessionmaker()() as session:
        yield session


async def create_tables() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database tables ensured")
