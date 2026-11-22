"""Links CRUD API tests (SQLite-backed; no network)."""

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.db.models import Check


async def _create(client: AsyncClient, url: str, label: str | None = None) -> dict:
    r = await client.post("/links", json={"url": url, "label": label})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_returns_201(client: AsyncClient) -> None:
    body = await _create(client, "https://example.com/", "Example")
    assert body["id"] > 0
    assert body["url"] == "https://example.com/"
    assert body["label"] == "Example"
    assert body["created_at"]
    assert body["last_status"] is None
    assert body["archive_url"] is None


async def test_create_duplicate_returns_409(client: AsyncClient) -> None:
    await _create(client, "https://example.com/")
    r = await client.post("/links", json={"url": "https://example.com/"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate"


async def test_create_invalid_url_returns_422(client: AsyncClient) -> None:
    r = await client.post("/links", json={"url": "not a url"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_create_non_http_scheme_returns_422(client: AsyncClient) -> None:
    r = await client.post("/links", json={"url": "ftp://example.com/file"})
    assert r.status_code == 422


async def test_create_too_long_label_returns_422(client: AsyncClient) -> None:
    r = await client.post(
        "/links", json={"url": "https://example.com/", "label": "x" * 201}
    )
    assert r.status_code == 422


async def test_list_empty(client: AsyncClient) -> None:
    r = await client.get("/links")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_newest_first(client: AsyncClient) -> None:
    first = await _create(client, "https://a.example/")
    second = await _create(client, "https://b.example/")
    r = await client.get("/links")
    assert [x["id"] for x in r.json()] == [second["id"], first["id"]]


async def test_get_existing(client: AsyncClient) -> None:
    created = await _create(client, "https://example.com/")
    r = await client.get(f"/links/{created['id']}")
    assert r.status_code == 200
    assert r.json()["url"] == "https://example.com/"


async def test_get_missing_returns_404(client: AsyncClient) -> None:
    r = await client.get("/links/9999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_get_non_integer_id_returns_422(client: AsyncClient) -> None:
    r = await client.get("/links/abc")
    assert r.status_code == 422


async def test_delete_then_get_returns_404(client: AsyncClient) -> None:
    created = await _create(client, "https://example.com/")
    r = await client.delete(f"/links/{created['id']}")
    assert r.status_code == 204
    assert r.content == b""
    assert (await client.get(f"/links/{created['id']}")).status_code == 404


async def test_delete_missing_returns_404(client: AsyncClient) -> None:
    r = await client.delete("/links/9999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_delete_cascades_to_checks(
    client: AsyncClient, session: AsyncSession
) -> None:
    created = await _create(client, "https://example.com/")
    session.add(Check(link_id=created["id"], status_code=200, latency_ms=12))
    await session.commit()

    assert (await client.delete(f"/links/{created['id']}")).status_code == 204

    count = await session.scalar(select(func.count()).select_from(Check))
    assert count == 0
