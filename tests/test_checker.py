"""Checker tests (outbound HTTP mocked with respx, DNS faked).

Mocks use the IP address, because that is what the checker actually requests.
"""

import asyncio
import ssl

import httpx
import pytest
import respx

from gcpbasicdeployment.services.checker import Outcome, check_many

BASE = "https://93.184.216.34"
PAGE = "https://example.com/page"


async def _check(url: str = PAGE, timeout: float = 5.0) -> Outcome:
    [outcome] = await check_many([url], timeout=timeout, max_concurrency=5)
    return outcome


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (200, "alive"),
        (204, "alive"),
        (401, "alive"),
        (403, "alive"),
        (429, "alive"),
        (404, "dead"),
        (410, "dead"),
        (500, "dead"),
    ],
)
@respx.mock
async def test_status_codes(code: int, status: str) -> None:
    respx.get(f"{BASE}/page").respond(code)
    outcome = await _check()
    assert (outcome.status, outcome.status_code, outcome.error) == (status, code, None)
    assert outcome.latency_ms is not None


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "connection"),
        (httpx.ReadError("reset"), "connection"),
        (RuntimeError("boom"), "unknown"),
    ],
)
@respx.mock
async def test_request_failures(exception: Exception, error: str) -> None:
    respx.get(f"{BASE}/page").mock(side_effect=exception)
    outcome = await _check()
    assert (outcome.status, outcome.status_code, outcome.error) == ("dead", None, error)


async def test_ssl_failure(
    monkeypatch: pytest.MonkeyPatch, fake_dns: dict[str, list[str]]
) -> None:
    # respx overwrites an exception's __cause__, so fake `send` itself to keep the
    # chain that a real TLS failure has.
    fake_dns["two.example"] = ["93.184.216.34", "93.184.216.35"]
    attempts = 0

    async def send(*args: object, **kwargs: object) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("tls") from ssl.SSLCertVerificationError("expired")

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    assert (await _check("https://two.example/")).error == "ssl"
    assert attempts == 1  # a certificate problem is not retried on other addresses


async def test_dns_failure() -> None:
    outcome = await _check("https://nope.invalid/")
    assert (outcome.status, outcome.error) == ("dead", "dns")


@respx.mock
async def test_blocked_url_is_never_requested() -> None:
    route = respx.route().respond(200)
    outcome = await _check("http://localhost:8000/health")
    assert outcome == Outcome(status="blocked", error="blocked_url")
    assert not route.called


@respx.mock
async def test_sends_to_validated_ip_with_original_host() -> None:
    route = respx.get("https://93.184.216.34:8443/page?q=1").respond(200)
    assert (await _check("https://example.com:8443/page?q=1")).status == "alive"
    request = route.calls.last.request
    assert request.headers["host"] == "example.com:8443"
    assert request.extensions["sni_hostname"] == "example.com"


@respx.mock
async def test_tries_next_address_when_connection_fails(
    fake_dns: dict[str, list[str]],
) -> None:
    fake_dns["two.example"] = ["93.184.216.34", "93.184.216.35"]
    respx.get(f"{BASE}/").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("https://93.184.216.35/").respond(200)
    assert (await _check("https://two.example/")).status == "alive"


@respx.mock
async def test_follows_relative_redirect() -> None:
    respx.get(f"{BASE}/page").respond(301, headers={"Location": "/new"})
    target = respx.get(f"{BASE}/new").respond(200)
    outcome = await _check()
    assert (outcome.status, outcome.status_code) == ("alive", 200)
    assert target.calls.last.request.headers["host"] == "example.com"


@respx.mock
async def test_follows_redirect_to_another_public_host(
    fake_dns: dict[str, list[str]],
) -> None:
    fake_dns["other.example"] = ["8.8.8.8"]
    respx.get(f"{BASE}/page").respond(
        302, headers={"Location": "https://other.example/x"}
    )
    target = respx.get("https://8.8.8.8/x").respond(200)
    assert (await _check()).status == "alive"
    assert target.calls.last.request.headers["host"] == "other.example"


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://localhost/",
        "//10.0.0.1/",
        "ftp://example.com/file",
    ],
)
@respx.mock
async def test_redirect_to_forbidden_target_is_blocked(location: str) -> None:
    first = respx.get(f"{BASE}/page").respond(302, headers={"Location": location})
    other = respx.route().respond(200)
    outcome = await _check()
    assert outcome == Outcome(status="blocked", error="blocked_url")
    assert first.call_count == 1
    assert not other.called


@respx.mock
async def test_redirect_without_location_is_dead() -> None:
    respx.get(f"{BASE}/page").respond(302)
    outcome = await _check()
    assert (outcome.status, outcome.status_code) == ("dead", 302)


@respx.mock
async def test_five_redirects_are_allowed() -> None:
    for i in range(5):
        respx.get(f"{BASE}/r{i}").respond(302, headers={"Location": f"/r{i + 1}"})
    respx.get(f"{BASE}/r5").respond(200)
    assert (await _check("https://example.com/r0")).status == "alive"


@respx.mock
async def test_redirect_loop() -> None:
    route = respx.get(f"{BASE}/page").respond(302, headers={"Location": "/page"})
    outcome = await _check()
    assert (outcome.status, outcome.error) == ("dead", "too_many_redirects")
    assert route.call_count == 6


@respx.mock
async def test_cookies_are_not_kept() -> None:
    respx.get(f"{BASE}/page").respond(
        302, headers={"Location": "/new", "Set-Cookie": "a=1; Path=/"}
    )
    target = respx.get(f"{BASE}/new").respond(200)
    await _check()
    assert "cookie" not in target.calls.last.request.headers


@respx.mock
async def test_concurrency_is_capped_and_order_is_kept() -> None:
    running = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return httpx.Response(int(request.url.path.strip("/")))

    respx.route().mock(side_effect=slow)
    codes = [200, 404, 200, 500, 200, 404, 200]
    urls = [f"https://example.com/{code}" for code in codes]
    outcomes = await check_many(urls, timeout=5.0, max_concurrency=3)
    assert peak == 3
    assert [o.status_code for o in outcomes] == codes


@respx.mock
async def test_whole_check_time_limit() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    respx.get(f"{BASE}/page").mock(side_effect=hang)
    assert (await _check(timeout=0.05)).error == "timeout"


@respx.mock
async def test_waiting_for_a_slot_does_not_count_against_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.15)
        return httpx.Response(200)

    respx.route().mock(side_effect=slow)
    outcomes = await check_many([PAGE] * 3, timeout=0.4, max_concurrency=1)
    assert [o.status for o in outcomes] == ["alive"] * 3
