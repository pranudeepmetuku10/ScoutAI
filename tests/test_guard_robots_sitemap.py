import pytest

from scoutai.crawl.guard import BlockedHostError, HostGuard, is_public_ip
from scoutai.crawl.robots import robots_from_response
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
