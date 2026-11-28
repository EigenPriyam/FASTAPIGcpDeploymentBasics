"""Check endpoints (SQLite-backed; HTTP mocked with respx, DNS faked)."""

import httpx
import respx
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from gcpbasicdeployment.db.models import Link
from gcpbasicdeployment.services.archive import API_URL

BASE = "https://93.184.216.34"
SNAPSHOT = "https://web.archive.org/web/20260101000000/https://example.com/gone"
FOUND = {
    "archived_snapshots": {
        "closest": {"status": "200", "available": True, "url": SNAPSHOT}
    }
}
NOT_FOUND: dict[str, dict] = {"archived_snapshots": {}}


def _wayback(reply: dict = NOT_FOUND) -> respx.Route:
    return respx.get(API_URL).respond(json=reply)


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
    wayback = _wayback()
    link_id = await _create(client, "https://example.com/slow")

    r = await client.post(f"/links/{link_id}/check")

    assert r.status_code == 200
    assert r.json()["last_status"] == "dead"
    assert r.json()["check"]["error"] == "timeout"
    assert r.json()["archive_url"] is None
    assert wayback.call_count == 1


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
    _wayback()
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
    wayback = _wayback()
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
    asked = sorted(call.request.url.params["url"] for call in wayback.calls)
    assert asked == ["https://example.com/gone", "https://nope.invalid/"]
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


async def _check(client: AsyncClient, link_id: int) -> dict:
    r = await client.post(f"/links/{link_id}/check")
    assert r.status_code == 200, r.text
    return r.json()


async def _stored_archive_url(client: AsyncClient, link_id: int) -> str | None:
    return (await client.get(f"/links/{link_id}")).json()["archive_url"]


@respx.mock
async def test_dead_link_gets_archive_url_and_is_not_asked_again(
    client: AsyncClient,
) -> None:
    respx.get(f"{BASE}/gone").respond(404)
    wayback = _wayback(FOUND)
    link_id = await _create(client, "https://example.com/gone")

    assert (await _check(client, link_id))["archive_url"] == SNAPSHOT
    assert await _stored_archive_url(client, link_id) == SNAPSHOT
    assert (await _check(client, link_id))["archive_url"] == SNAPSHOT
    assert wayback.call_count == 1


@respx.mock
async def test_archive_url_is_cleared_when_link_is_alive_again(
    client: AsyncClient,
) -> None:
    respx.get(f"{BASE}/gone").mock(
        side_effect=[httpx.Response(404), httpx.Response(200)]
    )
    wayback = _wayback(FOUND)
    link_id = await _create(client, "https://example.com/gone")
    await _check(client, link_id)

    body = await _check(client, link_id)

    assert (body["last_status"], body["archive_url"]) == ("alive", None)
    assert await _stored_archive_url(client, link_id) is None
    assert wayback.call_count == 1


@respx.mock
async def test_blocked_link_is_never_sent_and_keeps_its_copy(
    client: AsyncClient, fake_dns: dict[str, list[str]]
) -> None:
    respx.get(f"{BASE}/gone").respond(404)
    wayback = _wayback(FOUND)
    link_id = await _create(client, "https://example.com/gone")
    await _check(client, link_id)
    fake_dns["example.com"] = ["10.0.0.5"]  # the name now points inside

    body = await _check(client, link_id)

    assert (body["last_status"], body["archive_url"]) == ("blocked", SNAPSHOT)
    local = await _create(client, "http://localhost/")
    assert (await _check(client, local))["archive_url"] is None
    assert wayback.call_count == 1


@respx.mock
async def test_wayback_down_still_saves_the_check_and_retries_later(
    client: AsyncClient,
) -> None:
    respx.get(f"{BASE}/gone").respond(404)
    wayback = respx.get(API_URL).mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200, json=FOUND)]
    )
    link_id = await _create(client, "https://example.com/gone")

    first = await _check(client, link_id)

    assert (first["last_status"], first["archive_url"]) == ("dead", None)
    assert len((await client.get(f"/links/{link_id}/checks")).json()) == 1
    assert (await _check(client, link_id))["archive_url"] == SNAPSHOT
    assert wayback.call_count == 2


@respx.mock
async def test_check_all_looks_up_each_dead_link(client: AsyncClient) -> None:
    respx.get(f"{BASE}/a").respond(404)
    respx.get(f"{BASE}/b").respond(500)
    wayback = _wayback(FOUND)
    await _create(client, "https://example.com/a")
    await _create(client, "https://example.com/b")

    r = await client.post("/check-all")

    assert [x["archive_url"] for x in r.json()["results"]] == [SNAPSHOT, SNAPSHOT]
    assert wayback.call_count == 2
    stored = [x["archive_url"] for x in (await client.get("/links")).json()]
    assert stored == [SNAPSHOT, SNAPSHOT]
