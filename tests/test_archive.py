"""Wayback client tests (outbound HTTP mocked with respx)."""

import asyncio
import logging

import httpx
import pytest
import respx

from gcpbasicdeployment.services.archive import API_URL, find_snapshots

PAGE = "https://example.com/old?page=2"
SNAPSHOT = "http://web.archive.org/web/20260101000000/https://example.com/old?page=2"
STORED = "https://web.archive.org/web/20260101000000/https://example.com/old?page=2"


def _reply(**changes: object) -> dict[str, object]:
    closest = {"status": "200", "available": True, "url": SNAPSHOT} | changes
    return {"url": PAGE, "archived_snapshots": {"closest": closest}}


async def _find(url: str = PAGE, timeout: float = 5.0) -> str | None:
    return (await find_snapshots([(1, url)], timeout)).get(1)


@respx.mock
async def test_copy_found_is_stored_as_https() -> None:
    route = respx.get(API_URL, params={"url": PAGE}).respond(json=_reply())
    assert await _find() == STORED
    assert route.called


@respx.mock
async def test_fragment_is_not_sent() -> None:
    route = respx.get(API_URL).respond(json=_reply())
    await _find(f"{PAGE}#secret")
    assert route.calls.last.request.url.params["url"] == PAGE


@respx.mock
async def test_no_copy(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(API_URL).respond(json={"url": PAGE, "archived_snapshots": {}})
    with caplog.at_level(logging.WARNING):
        assert await _find() is None
    assert "archive lookup failed" not in caplog.text


@pytest.mark.parametrize(
    "changes",
    [
        {"available": False},
        {"status": "404"},
        {"url": "https://evil.example/web/x"},
        {"url": "https://web.archive.org.evil.example/web/x"},
        {"url": "https://user@web.archive.org/web/x"},
        {"url": "ftp://web.archive.org/web/x"},
        {"url": None},
    ],
)
@respx.mock
async def test_unusable_snapshot_is_ignored(changes: dict[str, object]) -> None:
    respx.get(API_URL).respond(json=_reply(**changes))
    assert await _find() is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(302, headers={"Location": "https://example.com/"}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, json={"archived_snapshots": {"closest": "text"}}),
    ],
)
@respx.mock
async def test_bad_reply_is_swallowed_and_logged(
    response: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(API_URL).mock(return_value=response)
    logging.getLogger().addHandler(caplog.handler)
    assert await _find() is None
    assert "archive lookup failed id=1" in caplog.text
    assert "example.com/old" not in caplog.text


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("slow"), httpx.ConnectError(f"cannot reach {PAGE}")]
)
@respx.mock
async def test_network_error_is_swallowed_without_logging_url(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(API_URL).mock(side_effect=error)
    logging.getLogger().addHandler(caplog.handler)
    assert await _find() is None
    assert "archive lookup failed id=1" in caplog.text
    assert "example.com/old" not in caplog.text


@respx.mock
async def test_whole_lookup_time_limit() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=_reply())

    respx.get(API_URL).mock(side_effect=hang)
    assert await _find(timeout=0.05) is None


@respx.mock
async def test_lookups_are_capped_and_matched_to_their_links() -> None:
    running = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        asked = request.url.params["url"]
        return httpx.Response(
            200, json=_reply(url=f"https://web.archive.org/web/1/{asked}")
        )

    respx.get(API_URL).mock(side_effect=slow)
    targets = [(i, f"https://example.com/{i}") for i in range(1, 8)]
    found = await find_snapshots(targets, 5.0)

    assert peak == 3
    assert found == {i: f"https://web.archive.org/web/1/{url}" for i, url in targets}


@respx.mock
async def test_no_targets_makes_no_request() -> None:
    route = respx.route().respond(200)
    assert await find_snapshots([], 5.0) == {}
    assert not route.called
