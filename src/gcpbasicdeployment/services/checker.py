"""Async URL checker (httpx.AsyncClient + asyncio.Semaphore, explicit timeouts).

No database access here: it takes URLs and returns plain Outcome objects.
"""

import asyncio
import logging
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Literal

import httpx

from gcpbasicdeployment.services.url_safety import (
    BlockedURLError,
    DNSError,
    resolve_safe,
)

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
REDIRECT_CODES = {301, 302, 303, 307, 308}
# The page exists but refuses a bot or is rate limiting.
ALIVE_EXTRA = {401, 403, 429}
USER_AGENT = "LinkRotWatchdog/0.1"

Status = Literal["alive", "dead", "blocked"]


class TooManyRedirects(Exception):
    pass


@dataclass(frozen=True)
class Outcome:
    status: Status
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None


def build_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,  # every hop must pass the SSRF guard
        trust_env=False,  # a proxy would resolve the name itself
        # Requests go to IP addresses; never reuse one host's connection for another.
        limits=httpx.Limits(max_keepalive_connections=0),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        headers={"User-Agent": USER_AGENT},
    )


async def _send(
    client: httpx.AsyncClient, url: httpx.URL, addresses: list[str]
) -> httpx.Response:
    """GET the URL on an already validated IP address, trying each in turn.

    The Host header and TLS name stay the original host, so the site and the
    certificate check behave as if we had connected by name.
    """
    last_error: httpx.ConnectError | None = None
    for address in addresses:
        request = client.build_request(
            "GET",
            url.copy_with(host=address),
            headers={"Host": url.netloc.decode("ascii")},
            extensions={"sni_hostname": url.raw_host.decode("ascii")},
        )
        try:
            # stream=True: read the status and headers, never the body.
            return await client.send(request, stream=True)
        except httpx.ConnectError as exc:
            if _is_ssl_error(exc):
                raise
            last_error = exc
    assert last_error is not None  # resolve_safe never returns an empty list
    raise last_error


async def _follow(client: httpx.AsyncClient, raw_url: str) -> int:
    """Request a URL, following redirects by hand. Returns the final status code."""
    url = httpx.URL(raw_url)
    for _ in range(MAX_REDIRECTS + 1):
        addresses = await resolve_safe(url)
        response = await _send(client, url, addresses)
        await response.aclose()

        location = response.headers.get("location")
        if response.status_code not in REDIRECT_CODES or not location:
            return response.status_code
        try:
            # Join against the host-name URL, not the IP one.
            url = url.join(location)
        except httpx.InvalidURL as exc:
            raise BlockedURLError("invalid redirect target") from exc
    raise TooManyRedirects


def _is_ssl_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


async def check_url(client: httpx.AsyncClient, url: str, timeout: float) -> Outcome:
    """Check one URL. A broken target is a result, not an exception."""
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return round((time.perf_counter() - started) * 1000)

    def failed(error: str) -> Outcome:
        return Outcome(status="dead", latency_ms=elapsed_ms(), error=error)

    try:
        # Limits the whole check; the httpx timeout only limits each network step.
        async with asyncio.timeout(timeout):
            status_code = await _follow(client, url)
    except BlockedURLError:
        return Outcome(status="blocked", error="blocked_url")
    except DNSError:
        return failed("dns")
    except (TimeoutError, httpx.TimeoutException):
        return failed("timeout")
    except TooManyRedirects:
        return failed("too_many_redirects")
    except httpx.RequestError as exc:
        return failed("ssl" if _is_ssl_error(exc) else "connection")
    except Exception:
        logger.exception("unexpected error while checking a link")
        return failed("unknown")

    alive = 200 <= status_code < 300 or status_code in ALIVE_EXTRA
    return Outcome(
        status="alive" if alive else "dead",
        status_code=status_code,
        latency_ms=elapsed_ms(),
    )


async def check_many(
    urls: Sequence[str], timeout: float, max_concurrency: int
) -> list[Outcome]:
    """Check URLs concurrently. Results are in the same order as `urls`."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async with build_client(timeout) as client:

        async def limited(url: str) -> Outcome:
            # The timeout starts inside check_url, after a slot is free.
            async with semaphore:
                return await check_url(client, url, timeout)

        return await asyncio.gather(*(limited(url) for url in urls))
