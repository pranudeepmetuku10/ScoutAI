"""HTTP fetching with a response size cap, charset sniffing, and per-request SSRF/rate-limit hooks."""

from __future__ import annotations

import asyncio
import gzip
import re
from dataclasses import dataclass

import httpx

from scoutai.config import USER_AGENT
from scoutai.crawl.guard import HostGuard

META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


class ResponseTooLargeError(Exception):
    pass


class HostRateLimiter:
    """Spaces requests to the same host at least `delay` seconds apart, across concurrent callers."""

    def __init__(self, delay: float):
        self.default_delay = delay
        self._delays: dict[str, float] = {}
        self._next_slot: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def set_delay(self, host: str, delay: float) -> None:
        self._delays[host] = delay

    async def wait(self, host: str) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + self._delays.get(host, self.default_delay)
        if slot > now:
            await asyncio.sleep(slot - now)


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes
    encoding: str
    etag: str | None = None
    last_modified: str | None = None

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or not self.content_type

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding, errors="replace")


def _encoding(response: httpx.Response, body: bytes) -> str:
    if response.charset_encoding:
        return response.charset_encoding
    if match := META_CHARSET.search(body[:4096]):
        name = match.group(1).decode("ascii", errors="ignore")
        try:
            "".encode(name)
            return name
        except LookupError:
            pass
    return "utf-8"


def build_client(
    guard: HostGuard,
    limiter: HostRateLimiter,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    async def on_request(request: httpx.Request) -> None:
        # Runs for every request, including each redirect hop.
        await guard.check(request.url.host)
        await limiter.wait(request.url.host)

    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"},
        follow_redirects=True,
        max_redirects=5,
        timeout=timeout,
        transport=transport,
        event_hooks={"request": [on_request]},
    )


async def fetch(client: httpx.AsyncClient, url: str, *, max_bytes: int) -> FetchResult:
    async with client.stream("GET", url) as response:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLargeError(f"response is {int(declared):,} bytes (limit {max_bytes:,})")
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ResponseTooLargeError(f"response exceeds {max_bytes:,} bytes")
            chunks.append(chunk)
    body = b"".join(chunks)
    if str(response.url).endswith(".gz") and body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)[:max_bytes]
    return FetchResult(
        url=str(response.url),
        status=response.status_code,
        content_type=response.headers.get("content-type", "").lower(),
        body=body,
        encoding=_encoding(response, body),
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
