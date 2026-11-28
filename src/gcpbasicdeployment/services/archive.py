"""Wayback Machine availability client; degrades gracefully when down."""

import asyncio
import logging
from collections.abc import Sequence
from urllib.parse import urldefrag

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://archive.org/wayback/available"
SNAPSHOT_HOST = "web.archive.org"
# Be polite to a free service.
MAX_LOOKUPS = 3


def _valid_snapshot(closest: dict[str, object]) -> str | None:
    """The snapshot address, as https, if it is safe and useful to store."""
    if closest.get("available") is not True:
        return None
    if not str(closest.get("status", "")).startswith("2"):
        return None
    address = closest.get("url")
    if not isinstance(address, str):
        return None
    parsed = httpx.URL(address)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.host != SNAPSHOT_HOST
        or parsed.userinfo
    ):
        return None
    # Change only the scheme; the rest contains the original URL.
    return "https://" + address.split("://", 1)[1]


async def find_snapshot(
    client: httpx.AsyncClient, link_id: int, url: str, timeout: float
) -> str | None:
    """Closest archived copy of `url`, or None. Never raises."""
    try:
        async with asyncio.timeout(timeout):
            response = await client.get(API_URL, params={"url": urldefrag(url).url})
        if response.status_code != 200:
            logger.warning(
                "archive lookup failed id=%s code=%s", link_id, response.status_code
            )
            return None
        closest = response.json()["archived_snapshots"].get("closest")
        return None if closest is None else _valid_snapshot(closest)
    except Exception as exc:
        # Type only: the exception text can contain the URL.
        logger.warning(
            "archive lookup failed id=%s error=%s", link_id, type(exc).__name__
        )
        return None


async def find_snapshots(
    targets: Sequence[tuple[int, str]], timeout: float
) -> dict[int, str]:
    """Look up (link_id, url) pairs. Only links with a copy are in the result."""
    if not targets:
        return {}
    semaphore = asyncio.Semaphore(MAX_LOOKUPS)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout), follow_redirects=False
    ) as client:

        async def limited(link_id: int, url: str) -> str | None:
            async with semaphore:
                return await find_snapshot(client, link_id, url, timeout)

        found = await asyncio.gather(*(limited(i, u) for i, u in targets))

    return {
        link_id: snapshot
        for (link_id, _), snapshot in zip(targets, found, strict=True)
        if snapshot is not None
    }
