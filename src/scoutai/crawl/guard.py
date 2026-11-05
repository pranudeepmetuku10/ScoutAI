"""SSRF guard: refuse to fetch hosts that resolve to private, loopback or otherwise internal addresses."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpcore

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
    """`check` caches one verdict per host, for fast, clear errors. `resolve_public` re-resolves on every
    call and is what `PinnedNetworkBackend` uses at connect time, which closes the DNS-rebinding gap."""

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

    async def resolve_public(self, host: str) -> list[str]:
        """Fresh addresses for `host`, all of them public; raises BlockedHostError otherwise."""
        host = host.lower().strip("[]")
        if self.allow_private:
            return await self._lookup(host)
        addresses, reason = await self._resolve(host)
        if reason:
            raise BlockedHostError(reason)
        return addresses

    async def _lookup(self, host: str) -> list[str]:
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return await self._resolver(host)

    async def _verdict(self, host: str) -> str | None:
        return (await self._resolve(host))[1]

    async def _resolve(self, host: str) -> tuple[list[str], str | None]:
        """(addresses, reason they're blocked or None)."""
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return [], f"{host} is a local hostname"
        try:
            addresses = await self._lookup(host)
        except OSError as e:
            return [], f"could not resolve {host}: {e}"
        if not addresses:
            return [], f"{host} did not resolve"
        private = [a for a in addresses if not is_public_ip(a)]
        if private:
            return addresses, f"{host} resolves to non-public address {private[0]}"
        return addresses, None


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connects only to addresses `HostGuard` approved in the same call.

    Without this, the guard resolves a name, approves it, and then the HTTP library resolves it *again*
    to connect, and a hostile DNS server can answer differently the second time (DNS rebinding). Here the
    checked IP is the one we dial. TLS still uses the hostname for SNI and certificate checks, because
    httpcore takes `server_hostname` from the request URL, not from the address we connect to.
    """

    def __init__(self, guard: HostGuard, inner: httpcore.AsyncNetworkBackend | None = None):
        self._guard = guard
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self._guard.resolve_public(host)
        error: Exception | None = None
        for address in addresses:  # try each approved address, like a normal client would
            try:
                return await self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                error = e
        raise error or httpcore.ConnectError(f"{host} did not resolve")

    async def connect_unix_socket(self, path: str, timeout=None, socket_options=None):
        raise BlockedHostError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
