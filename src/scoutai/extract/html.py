"""HTML -> clean Markdown plus page metadata, from a single parse.

Main content comes from trafilatura. When it finds too little, a structural fallback strips page chrome
(nav, headers, cookie banners, ...) and keeps link-light text blocks. The longer result wins.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import trafilatura
from lxml import html as lxml_html
from lxml.etree import ParserError, SubElement, _Element

MIN_PRIMARY_CHARS = 400
CHROME_TAGS = [
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "form",
    "nav",
    "header",
    "footer",
    "aside",
    "button",
    "input",
    "select",
    "textarea",
    "template",
    "canvas",
    "dialog",
]
CHROME_ROLES = {"navigation", "banner", "contentinfo", "search", "dialog", "menu", "menubar", "complementary"}
CHROME_HINT = re.compile(
    r"(^|[-_\s])(nav|navbar|menu|footer|header|sidebar|breadcrumbs?|cookie|consent|gdpr|banner|share|"
    r"social|advert|ads?|promo|popup|modal|newsletter|subscribe|toc|skip)([-_\s]|$)",
    re.IGNORECASE,
)
BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "td", "th", "dt", "dd")
SPA_MARKERS = re.compile(r'id=["\'](root|app|__next|__nuxt|svelte)["\']|ng-version=|data-reactroot', re.I)
XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)
UNFOLLOWABLE = ("mailto:", "tel:", "javascript:", "data:", "#")


@dataclass
class ExtractedPage:
    title: str
    markdown: str
    links: list[str] = field(default_factory=list)
    canonical: str | None = None
    noindex: bool = False
    nofollow: bool = False
    looks_js_rendered: bool = False


def parse_html(html: str) -> _Element | None:
    try:
        return lxml_html.document_fromstring(XML_DECLARATION.sub("", html, count=1))
    except (ParserError, ValueError):
        return None


def _text(el: _Element) -> str:
    return " ".join(el.text_content().split())


def _base_url(tree: _Element, url: str) -> str:
    base = tree.find(".//base[@href]")
    return urljoin(url, base.get("href").strip()) if base is not None else url


def extract_title(tree: _Element) -> str:
    for xpath in ('//meta[@property="og:title"]/@content', "//title/text()", "//h1"):
        found = tree.xpath(xpath)
        if found:
            value = found[0] if isinstance(found[0], str) else _text(found[0])
            value = " ".join(value.split())
            if value:
                return value
    return ""


def extract_links(tree: _Element, url: str) -> list[str]:
    """Absolute hrefs in document order, without duplicates."""
    base = _base_url(tree, url)
    seen: dict[str, None] = {}
    for a in tree.iterfind(".//a[@href]"):
        href = a.get("href", "").strip()
        if href and not href.lower().startswith(UNFOLLOWABLE):
            seen.setdefault(urljoin(base, href), None)
    return list(seen)


def _robots_meta(tree: _Element) -> set[str]:
    directives: set[str] = set()
    for content in tree.xpath('//meta[translate(@name, "ROBTS", "robts")="robots"]/@content'):
        directives |= {d.strip().lower() for d in content.split(",")}
    return directives


def _canonical(tree: _Element, url: str) -> str | None:
    hrefs = tree.xpath('//link[translate(@rel, "CANONICAL", "canonical")="canonical"]/@href')
    return urljoin(_base_url(tree, url), hrefs[0].strip()) if hrefs and hrefs[0].strip() else None


def _with_trafilatura(html: str, url: str) -> str:
    text = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        favor_recall=True,
        deduplicate=True,
    )
    return text or ""


def _flatten_code_blocks(tree: _Element) -> _Element:
    """Copy of `tree` where each <pre> holds only `<code>plain text</code>`.

    Syntax highlighters wrap every token in <span>s and often every line in an anchor link; trafilatura
    then scores the block as link-heavy junk and drops the code entirely.
    """
    tree = copy.deepcopy(tree)
    for pre in tree.iter("pre"):
        text = pre.text_content()
        for child in list(pre):
            pre.remove(child)
        pre.text = None
        SubElement(pre, "code").text = text
    return tree


def _is_chrome(el: _Element) -> bool:
    if el.tag in CHROME_TAGS or el.get("role", "").lower() in CHROME_ROLES:
        return True
    if el.tag in ("html", "body", "main", "article"):
        return False
    return bool(CHROME_HINT.search(f"{el.get('class', '')} {el.get('id', '')}"))


def _with_structure(tree: _Element) -> str:
    """Mutates `tree`; pass a copy."""
    for el in list(tree.iter()):
        if isinstance(el.tag, str) and el.getparent() is not None and _is_chrome(el):
            el.drop_tree()

    # lxml elements are falsy when they have no children, so test against None explicitly.
    root = next(iter(tree.xpath("//main | //article")), None)
    if root is None:
        root = tree.find("body")
    if root is None:
        root = tree
    lines: list[str] = []
    for el in root.iter(*BLOCK_TAGS):
        if el.tag != "pre" and any(child is not el for child in el.iter(*BLOCK_TAGS)):
            continue  # a nested block will emit this text
        if any(p.tag == "pre" for p in el.iterancestors()):
            continue
        text = _text(el)
        if not text:
            continue
        if el.tag in ("p", "li", "td", "th", "dd"):
            link_chars = sum(len(_text(a)) for a in el.iter("a"))
            if len(text) < 200 and link_chars / len(text) > 0.6:
                continue  # mostly links: navigation, not content
        if el.tag[0] == "h" and el.tag[1:].isdigit():
            lines.append("#" * int(el.tag[1]) + " " + text)
        elif el.tag == "li":
            lines.append("- " + text)
        elif el.tag == "pre":
            lines.append("```\n" + el.text_content().strip("\n") + "\n```")
        elif el.tag == "blockquote":
            lines.append("> " + text)
        else:
            lines.append(text)
    return "\n\n".join(lines)


def extract(html: str, url: str) -> ExtractedPage:
    tree = parse_html(html)
    if tree is None:
        return ExtractedPage(title="", markdown="")

    content = _flatten_code_blocks(tree)
    markdown = _with_trafilatura(lxml_html.tostring(content, encoding="unicode"), url)
    if len(markdown) < MIN_PRIMARY_CHARS:
        fallback = _with_structure(content)
        if len(fallback) > len(markdown):
            markdown = fallback

    robots = _robots_meta(tree)
    return ExtractedPage(
        title=extract_title(tree),
        markdown=markdown,
        links=extract_links(tree, url),
        canonical=_canonical(tree, url),
        noindex="noindex" in robots or "none" in robots,
        nofollow="nofollow" in robots or "none" in robots,
        looks_js_rendered=len(markdown) < MIN_PRIMARY_CHARS
        or (len(markdown) < 1500 and bool(SPA_MARKERS.search(html))),
    )
