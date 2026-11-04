import asyncio
import gzip

import httpx
import pytest

from scoutai.config import Settings
from scoutai.crawl import Crawler, CrawlError
from scoutai.crawl.fetcher import HostRateLimiter, ResponseTooLargeError, fetch

ORIGIN = "https://site.example"
FILLER = (
    "This paragraph exists so the extractor has enough real prose to treat the page as content. "
    "It talks about the product, its features, and how customers use it every day. "
) * 3


def html_page(title: str, *links: str, extra: str = "", head: str = "") -> str:
    anchors = "".join(
        f'<p>See <a href="{href}">{href}</a> for details about this topic.</p>' for href in links
    )
    return (
        f"<html><head><title>{title}</title>{head}</head><body><main><h1>{title}</h1>"
        f"<p>{title}: {FILLER}</p>{anchors}{extra}</main></body></html>"
    )


SITE = {
    "/robots.txt": (
        200,
        "text/plain",
        "User-agent: *\nDisallow: /secret\nSitemap: https://site.example/index.xml",
    ),
    "/index.xml": (
        200,
        "application/xml",
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://site.example/pages.xml.gz</loc></sitemap></sitemapindex>",
    ),
    "/": (
        200,
        "text/html",
        html_page(
            "Home",
            "/a",
            "/a/",
            "/b",
            "/secret",
            "/logo.png",
            "https://other.example/x",
            "/redirect",
            "/dup",
            "/noindex",
            "/missing",
            "/evil",
            "/feed",
        ),
    ),
    "/a": (200, "text/html", html_page("Page A", "/a/deep")),
    "/a/deep": (200, "text/html", html_page("Deep A", "/a/deeper")),
    "/a/deeper": (200, "text/html", html_page("Deeper A")),
    "/b": (200, "text/html", html_page("Page B")),
    "/dup": (200, "text/html", html_page("Copy of A", head='<link rel="canonical" href="/a">')),
    "/noindex": (
        200,
        "text/html",
        html_page("Tags", "/c", head='<meta name="robots" content="noindex">'),
    ),
    "/c": (200, "text/html", html_page("Page C")),
    "/from-sitemap": (200, "text/html", html_page("Sitemap only")),
    "/secret": (200, "text/html", html_page("Secret")),
    "/feed": (200, "application/json", "{}"),
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host != "site.example":
        return httpx.Response(200, text="should never be reached")
    path = request.url.path
    if path != "/":
        path = path.rstrip("/")  # like most servers, treat /a/ and /a as the same resource
    if path == "/redirect":
        return httpx.Response(301, headers={"location": "/b"})
    if path == "/evil":
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})
    if path == "/pages.xml.gz":
        xml = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://site.example/from-sitemap</loc></url>"
            "<url><loc>https://site.example/secret</loc></url></urlset>"
        )
        return httpx.Response(
            200, headers={"content-type": "application/gzip"}, content=gzip.compress(xml.encode())
        )
    if path not in SITE:
        return httpx.Response(404, text="not found")
    status, content_type, body = SITE[path]
    return httpx.Response(status, headers={"content-type": content_type}, text=body)


async def resolver(host: str) -> list[str]:
    return {"site.example": ["93.184.216.34"], "internal.example": ["10.0.0.7"]}[host]


def make_crawler(start: str = ORIGIN, **kwargs) -> Crawler:
    kwargs.setdefault("js_mode", "off")
    return Crawler(
        start,
        settings=Settings(request_delay=0.0),
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        **kwargs,
    )


def paths(result) -> list[str]:
    return [httpx.URL(p.url).path for p in result.pages]


async def test_crawl_respects_scope_robots_and_dedupes():
    result = await make_crawler(max_pages=50, max_depth=1).run()

    # BFS order: home, then its links in document order, then sitemap-only URLs.
    assert paths(result) == ["/", "/a", "/b", "/from-sitemap"]
    reasons = {httpx.URL(s.url).path: s.reason for s in result.skipped}
    assert reasons["/redirect"] == "duplicate of an already-crawled page"
    assert reasons["/dup"] == "duplicate of an already-crawled page"
    assert reasons["/noindex"] == "noindex"
    assert reasons["/missing"] == "HTTP 404"
    assert "non-public address" in reasons["/evil"]
    assert reasons["/feed"].startswith("not HTML")
    assert "/secret" not in reasons  # filtered before fetching, never requested


async def test_depth_limit_and_links_from_noindex_pages():
    result = await make_crawler(max_pages=50, max_depth=2).run()
    assert "/a/deep" in paths(result)
    assert "/c" in paths(result)  # discovered via the noindex page
    assert "/a/deeper" not in paths(result)


async def test_max_pages_and_determinism():
    first = await make_crawler(max_pages=3, max_depth=2).run()
    second = await make_crawler(max_pages=3, max_depth=2).run()
    assert paths(first) == paths(second) == ["/", "/a", "/b"]


async def test_same_path_restricts_crawl():
    result = await make_crawler(f"{ORIGIN}/a/", same_path=True, max_pages=50, max_depth=3).run()
    assert set(paths(result)) == {"/a/", "/a/deep", "/a/deeper"}


async def test_events_are_emitted():
    events = []

    async def emit(event, **data):
        events.append((event, data))

    await make_crawler(max_pages=2, max_depth=1, emit=emit).run()
    page_events = [d for e, d in events if e == "page"]
    assert [d["count"] for d in page_events] == [1, 2]


async def test_refuses_private_start_url():
    with pytest.raises(CrawlError):
        await make_crawler("http://internal.example/").run()
    with pytest.raises(CrawlError):
        Crawler("not a url at all")


async def test_js_auto_renders_thin_pages():
    class FakeRenderer:
        available = True

        async def render(self, url):
            return html_page("Rendered app")

        async def close(self):
            pass

    def spa(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, text='<div id="root"></div>')

    crawler = Crawler(
        ORIGIN,
        max_depth=0,
        settings=Settings(request_delay=0.0),
        transport=httpx.MockTransport(spa),
        resolver=resolver,
        renderer=FakeRenderer(),
    )
    result = await crawler.run()
    assert result.pages[0].rendered
    assert "Rendered app" in result.pages[0].markdown


async def test_fetch_enforces_size_limit_and_sniffs_charset():
    def big(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 2000)

    def latin1(request):
        body = '<meta charset="iso-8859-1"><p>café</p>'.encode("latin-1")
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(big)) as client:
        with pytest.raises(ResponseTooLargeError):
            await fetch(client, ORIGIN, max_bytes=1000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(latin1)) as client:
        assert "café" in (await fetch(client, ORIGIN, max_bytes=1000)).text


async def test_rate_limiter_spaces_requests():
    limiter = HostRateLimiter(0.05)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.gather(*(limiter.wait("h") for _ in range(4)))
    assert loop.time() - start >= 0.14
