"""SSRF guard tests, one or more per rule (DNS is faked)."""

import httpx
import pytest

from gcpbasicdeployment.services.url_safety import (
    BlockedURLError,
    DNSError,
    resolve_safe,
)


async def _assert_blocked(url: str) -> None:
    with pytest.raises(BlockedURLError):
        await resolve_safe(httpx.URL(url))


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd"])
async def test_rule1_non_http_scheme(url: str) -> None:
    await _assert_blocked(url)


async def test_rule2_missing_host() -> None:
    await _assert_blocked("http:///path")


async def test_rule3_credentials() -> None:
    await _assert_blocked("http://user:pw@example.com/")


@pytest.mark.parametrize(
    "url", ["http://localhost/", "http://127.0.0.1/", "http://[::1]/"]
)
async def test_rule4_loopback(url: str) -> None:
    await _assert_blocked(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        "http://[fd00::1]/",
    ],
)
async def test_rule5_private(url: str) -> None:
    await _assert_blocked(url)


@pytest.mark.parametrize("url", ["http://169.254.169.254/", "http://[fe80::1]/"])
async def test_rule6_link_local(url: str) -> None:
    await _assert_blocked(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://[::7f00:1]/",
        "http://[fec0::1]/",
    ],
)
async def test_rule7_other_non_public(url: str) -> None:
    await _assert_blocked(url)


@pytest.mark.parametrize("url", ["http://224.0.0.1/", "http://[ff02::1]/"])
async def test_rule8_multicast(url: str) -> None:
    await _assert_blocked(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:127.0.0.1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[2002:7f00:1::]/",
    ],
)
async def test_rule9_ipv4_wrapped_in_ipv6(url: str) -> None:
    await _assert_blocked(url)


async def test_public_looking_name_pointing_private_is_blocked(
    fake_dns: dict[str, list[str]],
) -> None:
    fake_dns["sneaky.example"] = ["10.0.0.5"]
    await _assert_blocked("https://sneaky.example/")


async def test_one_private_address_among_public_blocks(
    fake_dns: dict[str, list[str]],
) -> None:
    fake_dns["mixed.example"] = ["93.184.216.34", "127.0.0.1"]
    await _assert_blocked("https://mixed.example/")


@pytest.mark.parametrize("port", [0, 99999])
async def test_port_out_of_range(port: int) -> None:
    await _assert_blocked(f"http://example.com:{port}/")


async def test_public_address_is_allowed() -> None:
    assert await resolve_safe(httpx.URL("https://example.com/")) == ["93.184.216.34"]


async def test_addresses_are_unique_and_ipv4_first(
    fake_dns: dict[str, list[str]],
) -> None:
    v4, v6 = "93.184.216.34", "2606:2800:220:1::1"
    fake_dns["dual.example"] = [v6, v4, v4]
    assert await resolve_safe(httpx.URL("https://dual.example/")) == [v4, v6]


async def test_unresolvable_name_raises_dns_error() -> None:
    with pytest.raises(DNSError):
        await resolve_safe(httpx.URL("https://nope.invalid/"))
