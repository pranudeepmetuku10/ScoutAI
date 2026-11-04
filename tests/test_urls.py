import pytest

from scoutai.crawl.urls import has_skipped_extension, in_scope, normalize_url, path_prefix, site_host, url_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com//a///b/", "https://example.com/a/b/"),
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/?utm_source=x&b=2&a=1&fbclid=z", "https://example.com/?a=1&b=2"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_url_key_ignores_trailing_slash_except_root():
    assert url_key("https://example.com/docs/") == url_key("https://example.com/docs")
    assert url_key("https://example.com/") == "https://example.com/"


def test_site_host_strips_www():
    assert site_host("https://WWW.Example.com/x") == "example.com"


@pytest.mark.parametrize(
    ("path", "skipped"),
    [("/logo.PNG", True), ("/report.pdf", True), ("/docs/v1.2/intro", False), ("/blog/post", False)],
)
def test_has_skipped_extension(path, skipped):
    assert has_skipped_extension(path) is skipped


def test_path_prefix():
    assert path_prefix("https://example.com/docs/guide/intro") == "/docs/guide/"
    assert path_prefix("https://example.com/docs/") == "/docs/"
    assert path_prefix("https://example.com") == "/"


def test_in_scope():
    host = "example.com"
    assert in_scope("https://www.example.com/a", host=host)
    assert not in_scope("https://other.com/a", host=host)
    assert not in_scope("ftp://example.com/a", host=host)
    assert not in_scope("https://example.com/login", host=host)
    assert not in_scope("https://example.com/img/a.jpg", host=host)
    assert in_scope("https://example.com/docs/x", host=host, prefix="/docs/")
    assert in_scope("https://example.com/docs", host=host, prefix="/docs/")
    assert not in_scope("https://example.com/blog/x", host=host, prefix="/docs/")
