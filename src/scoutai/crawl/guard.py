"""SSRF guard: refuse to fetch hosts that resolve to private, loopback or otherwise internal addresses."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

Resolver = Callable[[str], Awaitable[list[str]]]


class BlockedHostError(Exception):
    """Raised when a URL points at an address the crawler must not reach."""


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class HostGuard:
    """Caches one verdict per host. Note: does not defend against DNS rebinding between check and fetch."""

    def __init__(self, *, allow_private: bool = False, resolver: Resolver = system_resolver):
        self.allow_private = allow_private
        self._resolver = resolver
        self._verdicts: dict[str, str | None] = {}  # host -> reason it's blocked, or None if allowed

    async def check(self, host: str) -> None:
        if self.allow_private:
            return
        host = host.lower().strip("[]")
        if host not in self._verdicts:
            self._verdicts[host] = await self._verdict(host)
        if reason := self._verdicts[host]:
            raise BlockedHostError(reason)

    async def _verdict(self, host: str) -> str | None:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return f"{host} is a local hostname"
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = await self._resolver(host)
            except OSError as e:
                return f"could not resolve {host}: {e}"
        if not addresses:
            return f"{host} did not resolve"
        private = [a for a in addresses if not is_public_ip(a)]
        if private:
            return f"{host} resolves to non-public address {private[0]}"
        return None
