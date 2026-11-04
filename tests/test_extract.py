from pathlib import Path

from scoutai.extract import extract

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_article_content_metadata_and_links():
    page = extract(load("article.html"), "https://acme.example/docs/pricing")

    assert page.title == "Pricing plans"
    assert page.canonical == "https://acme.example/pricing"
    assert "Starter plan costs twelve dollars" in page.markdown
    assert "Enterprise" in page.markdown
    assert "Accept all cookies" not in page.markdown
    assert "All rights reserved" not in page.markdown
    assert not page.noindex and not page.nofollow
    assert not page.looks_js_rendered

    # Relative links resolve against <base>; mailto/javascript are dropped; order is preserved.
    assert "https://acme.example/docs/getting-started" in page.links
    assert "https://acme.example/faq#billing" in page.links
    assert not any(link.startswith(("mailto:", "javascript:")) for link in page.links)
    assert page.links.index("https://acme.example/") < page.links.index("https://acme.example/about")


def test_spa_shell_looks_js_rendered():
    page = extract(load("spa_shell.html"), "https://app.example/")
    assert page.looks_js_rendered
    assert page.markdown == ""


def test_robots_meta_is_case_insensitive():
    page = extract(load("noindex.html"), "https://example.com/tags/x")
    assert page.noindex and page.nofollow


def test_structural_fallback_drops_link_heavy_blocks():
    page = extract(load("link_heavy.html"), "https://example.com/hours")
    assert "support desk is open" in page.markdown
    assert "Home Docs Blog" not in page.markdown


def test_garbage_input_does_not_raise():
    page = extract("", "https://example.com/")
    assert page.markdown == "" and page.links == []
    page = extract(
        '<?xml version="1.0" encoding="utf-8"?><html><body><p>hi</p></body></html>', "https://e.x/"
    )
    assert page.title == ""


def test_syntax_highlighted_code_with_line_anchors_is_kept():
    page = extract(load("highlighted_code.html"), "https://docs.example/install")
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in page.markdown
    assert "wget -qO- https://astral.sh/uv/install.sh | sh" in page.markdown
