"""Shared fixtures: in-memory SQLite stands in for Postgres; no real network."""

import ipaddress
import socket
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from gcpbasicdeployment.db.models import Base
from gcpbasicdeployment.db.session import get_session
from gcpbasicdeployment.main import app
from gcpbasicdeployment.services import url_safety

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replace DNS for every test. Add names to the returned dict as needed."""
    records: dict[str, list[str]] = {
        "example.com": [PUBLIC_IP],
        "localhost": ["::1", "127.0.0.1"],
    }

    async def lookup(host: str, port: int) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        if host not in records:
            raise socket.gaierror("name not known")
        return records[host]

    monkeypatch.setattr(url_safety, "_lookup", lookup)
    return records


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    @event.listens_for(eng.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
        # SQLite ignores ON DELETE CASCADE unless this is on.
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    # ASGITransport does not run lifespan, so Postgres is never touched.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
