"""SSRF guard: reject non-http(s) schemes and private/loopback/link-local resolution."""

import asyncio
import ipaddress
import socket

import httpx

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}
# NAT64: an IPv4 address hidden in the last 32 bits of an IPv6 address.
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class BlockedURLError(Exception):
    """The URL must not be requested."""


class DNSError(Exception):
    """The host name does not resolve."""


async def _lookup(host: str, port: int) -> list[str]:
    """Resolve a host name. The only DNS access, so tests replace this."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def is_public(address: str) -> bool:
    """True only for an address on the public internet."""
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in NAT64_PREFIX:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            ip = embedded
    # Python calls some non-public ranges "global" (multicast, old IPv6 forms such
    # as ::7f00:1 and fec0::/10), so exclude them explicitly.
    if ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
        return False
    return ip.is_global


async def resolve_safe(url: httpx.URL) -> list[str]:
    """Validate a URL and return the public IP addresses it may be requested on.

    The caller must connect to one of these addresses, not resolve the name again
    (otherwise a second DNS answer could point somewhere private).
    """
    if url.scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError("scheme is not http or https")
    if not url.host:
        raise BlockedURLError("URL has no host")
    if url.userinfo:
        raise BlockedURLError("URL contains credentials")
    port = url.port if url.port is not None else DEFAULT_PORTS[url.scheme]
    if not 1 <= port <= 65535:
        raise BlockedURLError("port is out of range")

    try:
        addresses = await _lookup(url.raw_host.decode("ascii"), port)
    except (OSError, UnicodeError) as exc:
        raise DNSError("host name did not resolve") from exc
    if not addresses:
        raise DNSError("host name did not resolve")

    if not all(is_public(address) for address in addresses):
        raise BlockedURLError("host resolves to a non-public address")

    unique = dict.fromkeys(addresses)
    # IPv4 first: many networks have no IPv6 route.
    return sorted(unique, key=lambda address: ":" in address)
