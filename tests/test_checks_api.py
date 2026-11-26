"""Check endpoints (SQLite-backed; HTTP mocked with respx, DNS faked)."""

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.db.models import Link

BASE = "https://93.184.216.34"


async def _create(client: AsyncClient, url: str) -> int:
    r = await client.post("/links", json={"url": url})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@respx.mock
async def test_check_link_saves_result_and_status(client: AsyncClient) -> None:
    respx.get(f"{BASE}/ok").respond(200)
    link_id = await _create(client, "https://example.com/ok")

    r = await client.post(f"/links/{link_id}/check")

    assert r.status_code == 200
    body = r.json()
    assert (body["link_id"], body["last_status"]) == (link_id, "alive")
    assert body["check"]["status_code"] == 200
    assert body["check"]["error"] is None
    assert body["check"]["checked_at"]
    assert (await client.get(f"/links/{link_id}")).json()["last_status"] == "alive"


@respx.mock
async def test_broken_target_is_a_result_not_a_server_error(
    client: AsyncClient,
) -> None:
    respx.get(f"{BASE}/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    link_id = await _create(client, "https://example.com/slow")

    r = await client.post(f"/links/{link_id}/check")

    assert r.status_code == 200
    assert r.json()["last_status"] == "dead"
    assert r.json()["check"]["error"] == "timeout"


async def test_check_missing_link_returns_404(client: AsyncClient) -> None:
    r = await client.post("/links/9999/check")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_check_non_integer_id_returns_422(client: AsyncClient) -> None:
    r = await client.post("/links/abc/check")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


@respx.mock
async def test_history_is_newest_first(client: AsyncClient) -> None:
    respx.get(f"{BASE}/page").mock(
        side_effect=[httpx.Response(200), httpx.Response(404)]
    )
    link_id = await _create(client, "https://example.com/page")
    await client.post(f"/links/{link_id}/check")
    await client.post(f"/links/{link_id}/check")

    r = await client.get(f"/links/{link_id}/checks")

    assert r.status_code == 200
    assert [c["status_code"] for c in r.json()] == [404, 200]
    assert (await client.get(f"/links/{link_id}")).json()["last_status"] == "dead"


async def test_history_of_unchecked_link_is_empty(client: AsyncClient) -> None:
    link_id = await _create(client, "https://example.com/")
    r = await client.get(f"/links/{link_id}/checks")
    assert (r.status_code, r.json()) == (200, [])


async def test_history_of_missing_link_returns_404(client: AsyncClient) -> None:
    r = await client.get("/links/9999/checks")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_history_non_integer_id_returns_422(client: AsyncClient) -> None:
    assert (await client.get("/links/abc/checks")).status_code == 422


@respx.mock
async def test_check_all_with_mixed_results(client: AsyncClient) -> None:
    respx.get(f"{BASE}/ok").respond(200)
    respx.get(f"{BASE}/gone").respond(404)
    ok = await _create(client, "https://example.com/ok")
    gone = await _create(client, "https://example.com/gone")
    nodns = await _create(client, "https://nope.invalid/")
    local = await _create(client, "http://localhost:8000/health")

    r = await client.post("/check-all")

    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["alive"], body["dead"], body["blocked"]) == (4, 1, 2, 1)
    results = {
        x["link_id"]: (x["last_status"], x["check"]["error"]) for x in body["results"]
    }
    assert results == {
        ok: ("alive", None),
        gone: ("dead", None),
        nodns: ("dead", "dns"),
        local: ("blocked", "blocked_url"),
    }
    stored = {x["id"]: x["last_status"] for x in (await client.get("/links")).json()}
    assert stored == {ok: "alive", gone: "dead", nodns: "dead", local: "blocked"}


async def test_check_all_with_no_links(client: AsyncClient) -> None:
    r = await client.post("/check-all")
    assert r.status_code == 200
    assert r.json() == {
        "total": 0,
        "alive": 0,
        "dead": 0,
        "blocked": 0,
        "results": [],
    }


@respx.mock
async def test_check_all_skips_link_deleted_mid_check(
    client: AsyncClient, session: AsyncSession
) -> None:
    keep = await _create(client, "https://example.com/keep")
    doomed = await _create(client, "https://example.com/doomed")

    async def delete_then_respond(request: httpx.Request) -> httpx.Response:
        await session.execute(delete(Link).where(Link.id == doomed))
        await session.commit()
        return httpx.Response(200)

    respx.get(f"{BASE}/keep").respond(200)
    respx.get(f"{BASE}/doomed").mock(side_effect=delete_then_respond)

    r = await client.post("/check-all")

    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert [x["link_id"] for x in r.json()["results"]] == [keep]


@respx.mock
async def test_single_check_of_link_deleted_mid_check_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    link_id = await _create(client, "https://example.com/doomed")

    async def delete_then_respond(request: httpx.Request) -> httpx.Response:
        await session.execute(delete(Link).where(Link.id == link_id))
        await session.commit()
        return httpx.Response(200)

    respx.get(f"{BASE}/doomed").mock(side_effect=delete_then_respond)

    r = await client.post(f"/links/{link_id}/check")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
