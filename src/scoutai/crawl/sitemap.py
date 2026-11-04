"""Sitemap parsing: `<urlset>` page lists and `<sitemapindex>` files that point at more sitemaps."""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree


@dataclass
class Sitemap:
    pages: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # nested sitemaps, from a <sitemapindex>


def parse_sitemap(content: bytes) -> Sitemap:
    """Parse sitemap XML, ignoring namespaces. Returns an empty Sitemap for anything unparsable."""
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
        root = etree.fromstring(content, parser=parser)
    except etree.XMLSyntaxError:
        return Sitemap()
    if root is None:
        return Sitemap()

    tag = etree.QName(root).localname
    locs = [
        el.text.strip()
        for el in root.iter("{*}loc")
        if el.text and el.text.strip().startswith(("http://", "https://"))
    ]
    if tag == "urlset":
        return Sitemap(pages=locs)
    if tag == "sitemapindex":
        return Sitemap(children=locs)
    return Sitemap()
