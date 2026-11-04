"""Optional headless-Chromium rendering for JavaScript-heavy pages (requires the `js` extra)."""

from __future__ import annotations

import asyncio
from typing import Any

from scoutai.config import USER_AGENT

BLOCKED_RESOURCES = {"image", "media", "font"}


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

    async def render(self, url: str) -> str | None:
        async with self._start_lock:
            if self.available is None:
                await self._start()
        if not self.available:
            return None
        async with self._pages:
            page = await self._browser.new_page(user_agent=USER_AGENT)
            try:
                await page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in BLOCKED_RESOURCES
                        else route.continue_()
                    ),
                )
                await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
                return await page.content()
            except Exception:
                return None
            finally:
                await page.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
