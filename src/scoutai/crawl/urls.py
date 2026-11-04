"""URL normalization and scope rules."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SKIPPED_EXTENSIONS = frozenset(
    [
        "png",
        "jpg",
        "jpeg",
        "gif",
        "svg",
        "webp",
        "ico",
        "bmp",
        "avif",
        "pdf",
        "zip",
        "gz",
        "tgz",
        "tar",
        "rar",
        "7z",
        "mp4",
        "mp3",
        "wav",
        "avi",
        "mov",
        "webm",
        "m4a",
        "css",
        "js",
        "mjs",
        "map",
        "json",
        "xml",
        "rss",
        "atom",
        "woff",
        "woff2",
        "ttf",
        "eot",
        "otf",
        "exe",
        "dmg",
        "pkg",
        "apk",
        "deb",
        "rpm",
        "csv",
        "xls",
        "xlsx",
        "doc",
        "docx",
        "ppt",
        "pptx",
    ]
)
SKIPPED_PATH = re.compile(
    r"/(login|log-in|signin|sign-in|signup|sign-up|register|logout|log-out|cart|checkout|account|"
    r"wp-admin|wp-json|wp-login\.php)(/|$)",
    re.IGNORECASE,
)
TRACKING_PARAM = re.compile(r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|ref|ref_src|_hs\w+)$", re.I)
DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Canonical form used for fetching: lowercase scheme/host, no default port, no fragment,
    no tracking params, sorted query, collapsed `//` and resolved `.`/`..` segments."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    netloc = host
    if parts.port and parts.port != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    trailing_slash = path.endswith("/")
    path = posixpath.normpath(path)
    if trailing_slash and path != "/":
        path += "/"

    params = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAM.match(k)
    ]
    query = urlencode(sorted(params))
    return urlunsplit((scheme, netloc, path, query, ""))


def url_key(url: str) -> str:
    """Identity for de-duplication: `/docs` and `/docs/` are the same page."""
    url = normalize_url(url)
    parts = urlsplit(url)
    if parts.path != "/" and parts.path.endswith("/"):
        return urlunsplit(parts._replace(path=parts.path.rstrip("/")))
    return url


def site_host(url: str) -> str:
    """Host with `www.` removed, so `www.example.com` and `example.com` count as one site."""
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def has_skipped_extension(path: str) -> bool:
    _, dot, ext = path.rpartition(".")
    return bool(dot) and "/" not in ext and ext.lower() in SKIPPED_EXTENSIONS


def path_prefix(url: str) -> str:
    """Directory of a URL's path: `/docs/guide/intro` -> `/docs/guide/`."""
    path = urlsplit(url).path or "/"
    return path[: path.rfind("/") + 1]


def in_scope(url: str, *, host: str, prefix: str = "") -> bool:
    """Same site, http(s), not a binary/auth page, and (optionally) under `prefix`."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or site_host(url) != host:
        return False
    path = parts.path or "/"
    if has_skipped_extension(path) or SKIPPED_PATH.search(path):
        return False
    return not prefix or (path + "/").startswith(prefix)
