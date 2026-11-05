import httpcore
import pytest

from scoutai.crawl.fetcher import HostRateLimiter, build_client
from scoutai.crawl.guard import BlockedHostError, HostGuard, PinnedNetworkBackend, is_public_ip
from scoutai.crawl.renderer import allow_request
from scoutai.crawl.robots import Robots, robots_from_response
from scoutai.crawl.sitemap import parse_sitemap


def fake_resolver(table):
    async def resolve(host):
        if host not in table:
            raise OSError("unknown host")
        return table[host]

    return resolve


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("10.0.0.5", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("192.168.1.1", False),
        ("::1", False),
        ("::ffff:10.0.0.1", False),
        ("2606:4700::1111", True),
    ],
)
def test_is_public_ip(address, public):
    assert is_public_ip(address) is public


async def test_guard_blocks_private_and_local_hosts():
    guard = HostGuard(
        resolver=fake_resolver({"good.example": ["93.184.216.34"], "bad.example": ["10.0.0.1"]})
    )
    await guard.check("good.example")
    for host in ("bad.example", "localhost", "127.0.0.1", "printer.local", "missing.example"):
        with pytest.raises(BlockedHostError):
            await guard.check(host)


async def test_guard_allow_private_skips_checks():
    guard = HostGuard(allow_private=True, resolver=fake_resolver({}))
    await guard.check("localhost")


def test_robots_rules_delay_and_sitemaps():
    robots = robots_from_response(
        200,
        "User-agent: *\nDisallow: /private/\nCrawl-delay: 2\nSitemap: https://example.com/sm.xml\n",
    )
    assert robots.can_fetch("https://example.com/docs")
    assert not robots.can_fetch("https://example.com/private/x")
    assert robots.crawl_delay == 2
    assert robots.sitemaps == ["https://example.com/sm.xml"]


def test_robots_status_conventions():
    assert robots_from_response(404, "").can_fetch("https://example.com/anything")
    assert not robots_from_response(503, "").can_fetch("https://example.com/anything")


def test_parse_urlset_and_index():
    urlset = parse_sitemap(
        b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc> https://example.com/a </loc></url><url><loc>https://example.com/b</loc></url></urlset>"
    )
    assert urlset.pages == ["https://example.com/a", "https://example.com/b"]
    index = parse_sitemap(
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://example.com/sm-1.xml</loc></sitemap></sitemapindex>"
    )
    assert index.children == ["https://example.com/sm-1.xml"]
    assert parse_sitemap(b"not xml at all").pages == []


def test_robots_wildcards_and_longest_match():
    robots = Robots.parse("User-agent: *\nDisallow: /*?sort=\nDisallow: /*.pdf$\nDisallow: /a\nAllow: /a/b")
    assert not robots.can_fetch("https://x.example/list?sort=asc")
    assert not robots.can_fetch("https://x.example/files/report.pdf")
    assert robots.can_fetch("https://x.example/files/report.pdf.html")
    assert robots.can_fetch("https://x.example/a/b")  # longer Allow beats shorter Disallow
    assert not robots.can_fetch("https://x.example/a/c")


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.dialed = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialed.append(host)
        if host == "8.8.8.8":
            raise httpcore.ConnectError("unreachable")
        return httpcore.AsyncMockStream([])


async def test_pinned_backend_dials_only_checked_addresses():
    inner = RecordingBackend()
    guard = HostGuard(resolver=fake_resolver({"ok.example": ["8.8.8.8", "93.184.216.34"]}))
    await PinnedNetworkBackend(guard, inner).connect_tcp("ok.example", 443)
    assert inner.dialed == ["8.8.8.8", "93.184.216.34"]  # IPs, never the hostname; falls through


async def test_pinned_backend_defeats_dns_rebinding():
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])  # public when checked, private when connecting

    async def rebinding(host):
        return next(answers)

    guard = HostGuard(resolver=rebinding)
    await guard.check("rebind.example")  # the early check passes...
    inner = RecordingBackend()
    with pytest.raises(BlockedHostError):  # ...but the connect-time resolution is what gets dialed
        await PinnedNetworkBackend(guard, inner).connect_tcp("rebind.example", 80)
    assert inner.dialed == []


async def test_default_client_uses_pinned_backend():
    """Guards against an httpx/httpcore upgrade silently dropping our network backend."""
    async with build_client(HostGuard(), HostRateLimiter(0), timeout=5) as client:
        assert isinstance(client._transport._pool._network_backend, PinnedNetworkBackend)


async def test_browser_requests_go_through_the_check():
    checked = []

    async def check(url, is_navigation):
        checked.append((url, is_navigation))
        if "internal" in url:
            raise BlockedHostError("private")

    assert await allow_request("https://site.example/app.js", "script", False, check)
    assert not await allow_request("http://internal.example/admin", "xhr", False, check)
    assert not await allow_request("https://site.example/a.png", "image", False, check)
    assert not await allow_request("file:///etc/passwd", "document", True, check)
    assert await allow_request("data:text/plain,hi", "other", False, check)
    assert [u for u, _ in checked] == ["https://site.example/app.js", "http://internal.example/admin"]
