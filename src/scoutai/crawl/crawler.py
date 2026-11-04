"""Polite, same-site, breadth-first crawler.

The crawl runs level by level and keeps results in discovery order, so the same site with the same
settings yields the same pages in the same order. That makes evals reproducible.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from scoutai.config import Settings, get_settings
from scoutai.crawl.fetcher import HostRateLimiter, build_client, fetch
from scoutai.crawl.guard import BlockedHostError, HostGuard, Resolver, system_resolver
from scoutai.crawl.renderer import JSRenderer
from scoutai.crawl.robots import Robots, robots_from_response
from scoutai.crawl.sitemap import parse_sitemap
from scoutai.crawl.urls import in_scope, normalize_url, path_prefix, site_host, url_key
from scoutai.extract import ExtractedPage, extract

JSMode = Literal["off", "auto", "always"]
Emit = Callable[..., Awaitable[None]]
MAX_SITEMAP_FILES = 10
VALID_HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*|[0-9a-f:.]+")


async def _no_emit(event: str, **data: object) -> None:
    return None


class CrawlError(Exception):
    """The crawl could not start (bad URL, blocked host)."""


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    depth: int
    links: list[str] = field(default_factory=list)
    rendered: bool = False
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def words(self) -> int:
        return len(self.markdown.split())


@dataclass
class Skip:
    url: str
    reason: str


@dataclass
class CrawlResult:
    start_url: str
    pages: list[Page]
    skipped: list[Skip]
    js_rendering_unavailable: bool = False


@dataclass
class _Fetched:
    page: Page | None
    skip: Skip | None = None
    extracted: ExtractedPage | None = None
    final_url: str = ""


class Crawler:
    def __init__(
        self,
        start_url: str,
        *,
        max_pages: int = 25,
        max_depth: int = 2,
        same_path: bool = False,
        js_mode: JSMode = "auto",
        concurrency: int = 6,
        settings: Settings | None = None,
        emit: Emit | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = system_resolver,
        renderer: JSRenderer | None = None,
    ):
        start_url = start_url.strip()
        if "://" not in start_url:
            start_url = "https://" + start_url
        self.start_url = normalize_url(start_url)
        parts = urlsplit(self.start_url)
        if parts.scheme not in ("http", "https") or not VALID_HOST.fullmatch(parts.hostname or ""):
            raise CrawlError(f"Not a valid website URL: {start_url}")

        self.max_pages = max_pages
        self.max_depth = max_depth
        self.js_mode = js_mode
        self.concurrency = concurrency
        self.settings = settings or get_settings()
        self.emit = emit or _no_emit
        self.host = site_host(self.start_url)
        self.prefix = path_prefix(self.start_url) if same_path else ""

        self._transport = transport
        self._guard = HostGuard(allow_private=self.settings.allow_private_hosts, resolver=resolver)
        self._limiter = HostRateLimiter(self.settings.request_delay)
        self._renderer = renderer or JSRenderer()
        self._robots = Robots.allow_all()
        self._client: httpx.AsyncClient | None = None
        self._js_warned = False

    # -- scope -----------------------------------------------------------------------------------

    def _allowed(self, url: str) -> bool:
        return in_scope(url, host=self.host, prefix=self.prefix) and self._robots.can_fetch(url)

    def _origin(self) -> str:
        parts = urlsplit(self.start_url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    # -- discovery -------------------------------------------------------------------------------

    async def _load_robots(self) -> Robots:
        try:
            response = await fetch(
                self._client, f"{self._origin()}/robots.txt", max_bytes=self.settings.max_response_bytes
            )
            return robots_from_response(response.status, response.text)
        except (httpx.HTTPError, BlockedHostError):
            return Robots.allow_all()

    async def _sitemap_urls(self) -> list[str]:
        """Page URLs from robots-declared sitemaps (or /sitemap.xml), following sitemap indexes."""
        queue = list(self._robots.sitemaps) or [f"{self._origin()}/sitemap.xml"]
        visited: set[str] = set()
        pages: list[str] = []
        limit = self.max_pages * 4
        while queue and len(visited) < MAX_SITEMAP_FILES and len(pages) < limit:
            sitemap_url = queue.pop(0)
            if sitemap_url in visited or site_host(sitemap_url) != self.host:
                continue
            visited.add(sitemap_url)
            try:
                response = await fetch(self._client, sitemap_url, max_bytes=self.settings.max_response_bytes)
            except (httpx.HTTPError, BlockedHostError, ValueError):
                continue
            if response.status != 200:
                continue
            parsed = parse_sitemap(response.body)
            queue.extend(parsed.children)
            pages.extend(normalize_url(u) for u in parsed.pages if self._allowed(normalize_url(u)))
        return list(dict.fromkeys(pages))[:limit]

    # -- fetching --------------------------------------------------------------------------------

    async def _render(self, url: str) -> str | None:
        await self._guard.check(urlsplit(url).hostname or "")
        html = await self._renderer.render(url)
        if html is None and self._renderer.available is False and not self._js_warned:
            self._js_warned = True
            await self.emit(
                "log",
                level="warn",
                message="JavaScript rendering unavailable (install the `js` extra and run "
                "`playwright install chromium`). Continuing with static HTML.",
            )
        return html

    async def _fetch_page(self, url: str, depth: int) -> _Fetched:
        final_url, extracted, etag, last_modified = url, None, None, None
        if self.js_mode != "always":
            response = await fetch(self._client, url, max_bytes=self.settings.max_response_bytes)
            if response.status >= 400:
                return _Fetched(None, Skip(url, f"HTTP {response.status}"))
            if not response.is_html:
                return _Fetched(None, Skip(url, f"not HTML ({response.content_type})"))
            final_url = normalize_url(response.url)
            if not self._allowed(final_url):
                return _Fetched(None, Skip(url, f"redirected out of scope to {final_url}"))
            extracted = extract(response.text, final_url)
            etag, last_modified = response.etag, response.last_modified

        rendered = False
        if self.js_mode == "always" or (self.js_mode == "auto" and extracted and extracted.looks_js_rendered):
            html = await self._render(final_url)
            if html:
                candidate = extract(html, final_url)
                if extracted is None or len(candidate.markdown) > len(extracted.markdown):
                    extracted, rendered = candidate, True
        if extracted is None:
            return _Fetched(None, Skip(url, "JavaScript rendering failed"))

        page = Page(
            url=final_url,
            title=extracted.title or final_url,
            markdown=extracted.markdown,
            depth=depth,
            links=[] if extracted.nofollow else extracted.links,
            rendered=rendered,
            etag=etag,
            last_modified=last_modified,
        )
        return _Fetched(page, extracted=extracted, final_url=final_url)

    async def _fetch_batch(self, batch: list[str], depth: int) -> list[_Fetched]:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(url: str) -> _Fetched:
            async with semaphore:
                try:
                    return await self._fetch_page(url, depth)
                except (httpx.HTTPError, BlockedHostError, ValueError, OSError) as e:
                    reason = str(e) or e.__class__.__name__
                    return _Fetched(None, Skip(url, reason[:200]))

        return await asyncio.gather(*(one(url) for url in batch))

    # -- main loop -------------------------------------------------------------------------------

    async def run(self) -> CrawlResult:
        try:
            await self._guard.check(urlsplit(self.start_url).hostname or "")
        except BlockedHostError as e:
            raise CrawlError(f"Refusing to crawl {self.start_url}: {e}") from e

        pages: list[Page] = []
        skipped: list[Skip] = []
        stored: set[str] = set()  # url_keys of stored pages, their redirect targets and canonicals
        seen: set[str] = {url_key(self.start_url)}  # url_keys ever queued

        async with build_client(
            self._guard, self._limiter, timeout=self.settings.request_timeout, transport=self._transport
        ) as client:
            self._client = client
            try:
                self._robots = await self._load_robots()
                delay = max(self.settings.request_delay, self._robots.crawl_delay)
                self._limiter.set_delay(urlsplit(self.start_url).hostname or "", delay)
                if self._robots.crawl_delay > self.settings.request_delay:
                    await self.emit(
                        "log", level="info", message=f"Respecting robots.txt Crawl-delay: {delay}s"
                    )
                if not self._robots.can_fetch(self.start_url):
                    raise CrawlError(f"robots.txt disallows crawling {self.start_url}")

                sitemap = await self._sitemap_urls() if self.max_depth > 0 else []
                if sitemap:
                    await self.emit("log", level="info", message=f"sitemap: {len(sitemap)} in-scope URLs")

                frontier = [self.start_url]
                for depth in range(self.max_depth + 1):
                    next_frontier: list[str] = []
                    if depth == 1:
                        frontier += [u for u in sitemap if url_key(u) not in seen]
                        seen.update(url_key(u) for u in sitemap)
                    while frontier and len(pages) < self.max_pages:
                        batch, frontier = (
                            frontier[: self.max_pages - len(pages)],
                            frontier[self.max_pages - len(pages) :],
                        )
                        for url, result in zip(batch, await self._fetch_batch(batch, depth), strict=True):
                            await self._collect(
                                url, result, pages, skipped, stored, seen, next_frontier, depth
                            )
                    frontier = next_frontier
                    if not frontier or len(pages) >= self.max_pages:
                        break
            finally:
                await self._renderer.close()

        return CrawlResult(
            start_url=self.start_url,
            pages=pages,
            skipped=skipped,
            js_rendering_unavailable=self._renderer.available is False,
        )

    async def _collect(
        self,
        url: str,
        result: _Fetched,
        pages: list[Page],
        skipped: list[Skip],
        stored: set[str],
        seen: set[str],
        next_frontier: list[str],
        depth: int,
    ) -> None:
        if result.page is None:
            assert result.skip is not None
            skipped.append(result.skip)
            await self.emit("skip", url=result.skip.url, reason=result.skip.reason)
            return

        page, extracted = result.page, result.extracted
        assert extracted is not None
        # Follow links even from pages we don't store (noindex, duplicates), unless nofollow.
        if depth < self.max_depth:
            for link in page.links:
                link = normalize_url(link)
                if url_key(link) not in seen and self._allowed(link):
                    seen.add(url_key(link))
                    next_frontier.append(link)

        keys = {url_key(url), url_key(page.url)}
        if extracted.canonical and self._allowed(normalize_url(extracted.canonical)):
            keys.add(url_key(extracted.canonical))
        seen.update(keys)
        reason = None
        if extracted.noindex:
            reason = "noindex"
        elif keys & stored:
            reason = "duplicate of an already-crawled page"
        elif len(pages) >= self.max_pages:
            reason = "page limit reached"
        if reason:
            skipped.append(Skip(url, reason))
            await self.emit("skip", url=url, reason=reason)
            return

        stored.update(keys)
        pages.append(page)
        await self.emit(
            "page",
            url=page.url,
            title=page.title,
            words=page.words,
            depth=page.depth,
            rendered=page.rendered,
            count=len(pages),
            max=self.max_pages,
        )
