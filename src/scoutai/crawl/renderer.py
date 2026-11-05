"""Optional headless-Chromium rendering for JavaScript-heavy pages (requires the `js` extra)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from scoutai.config import USER_AGENT
from scoutai.crawl.guard import BlockedHostError

BLOCKED_RESOURCES = {"image", "media", "font"}
INLINE_SCHEMES = {"data", "blob", "about"}  # never touch the network

# Called for every request the browser makes: (url, is_navigation). Raise BlockedHostError to block it.
RequestCheck = Callable[[str, bool], Awaitable[None]]


@dataclass
class RenderedPage:
    url: str  # where the browser ended up, after redirects and JavaScript navigation
    html: str


async def allow_request(url: str, resource_type: str, is_navigation: bool, check: RequestCheck) -> bool:
    """Whether the browser may make this request. Unknown schemes (file:, chrome:, ...) are refused."""
    if resource_type in BLOCKED_RESOURCES:
        return False
    parts = urlsplit(url)
    if parts.scheme in INLINE_SCHEMES:
        return True
    if parts.scheme not in ("http", "https", "ws", "wss") or not parts.hostname:
        return False
    try:
        await check(url, is_navigation)
    except BlockedHostError:
        return False
    return True


class JSRenderer:
    """Starts Playwright lazily on first use; `available` is None until then."""

    def __init__(self, *, max_pages: int = 2, timeout_ms: int = 25_000):
        self.available: bool | None = None
        self.error = ""
        self._timeout_ms = timeout_ms
        self._pages = asyncio.Semaphore(max_pages)
        self._start_lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None

    async def _start(self) -> None:
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
            self.available = True
        except Exception as e:  # not installed, or browsers not downloaded
            self.available = False
            self.error = (str(e).splitlines() or [e.__class__.__name__])[0][:200]

    async def render(self, url: str, check: RequestCheck) -> RenderedPage | None:
        """Every request the page makes (the document, redirects, scripts, XHR) goes through `check`, so
        the SSRF guard covers the browser too. Chrome still does its own DNS lookup, so DNS rebinding is
        only fully closed for the plain HTTP client."""
        async with self._start_lock:
            if self.available is None:
                await self._start()
        if not self.available:
            return None
        async with self._pages:
            page = await self._browser.new_page(user_agent=USER_AGENT)
            try:

                async def handle(route: Any) -> None:
                    request = route.request
                    if await allow_request(
                        request.url, request.resource_type, request.is_navigation_request(), check
                    ):
                        await route.continue_()
                    else:
                        await route.abort("blockedbyclient")

                await page.route("**/*", handle)
                await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
                return RenderedPage(url=page.url, html=await page.content())
            except Exception:
                return None
            finally:
                await page.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
