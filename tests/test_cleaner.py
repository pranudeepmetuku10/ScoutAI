from dataclasses import dataclass

from scoutai.clean import clean_pages

BODY = (
    "This page explains a distinct topic in enough detail to be worth keeping in the index for search, "
    "with a few more words so it clears the thin-page limit."
)


@dataclass
class P:
    url: str
    title: str
    markdown: str


def site(n: int, shared: str = "", per_page=lambda i: "") -> list[P]:
    return [
        P(
            f"https://x.example/{i}",
            f"Page {i}",
            f"# Page {i}\n\n{BODY} Topic number {i}.\n\n{per_page(i)}\n\n{shared}",
        )
        for i in range(n)
    ]


def test_boilerplate_on_half_the_pages_is_removed():
    pages = site(6, per_page=lambda i: "Join 10,000 readers getting our weekly digest." if i < 3 else "")
    cleaned, stats = clean_pages(pages)
    assert stats.boilerplate_blocks == 3
    assert all("weekly digest" not in p.markdown for p in cleaned)
    assert all(f"Topic number {i}" in p.markdown for i, p in enumerate(cleaned))


def test_repeat_below_threshold_is_kept():
    pages = site(6, per_page=lambda i: "Shared note on two pages only." if i < 2 else "")
    cleaned, stats = clean_pages(pages)
    assert stats.boilerplate_blocks == 0
    assert "Shared note" in cleaned[0].markdown


def test_small_sites_skip_boilerplate_detection():
    cleaned, stats = clean_pages(site(3, shared="Same footer text on every page here."))
    assert stats.boilerplate_blocks == 0
    assert all("Same footer" in p.markdown for p in cleaned)


def test_nav_list_items_removed_individually():
    nav = lambda i: f"- Home\n- Docs\n- Blog\n- Only on page {i}"  # noqa: E731
    cleaned, _ = clean_pages(site(5, per_page=nav))
    assert cleaned[0].markdown.count("- ") == 1
    assert "- Only on page 0" in cleaned[0].markdown


def test_headings_survive_but_orphans_are_dropped():
    related = "## Related posts\n\nAnother post you might like to read next."
    cleaned, stats = clean_pages(site(4, shared=related))
    assert "Related posts" not in cleaned[0].markdown  # its content was boilerplate, so it's an orphan
    assert stats.orphan_headings == 4
    assert cleaned[0].markdown.startswith("# Page 0")


def test_heading_with_subsections_is_not_an_orphan():
    md = f"# Guide\n\n## Install\n\n{BODY}\n\n## Empty\n\n### Also empty"
    cleaned, stats = clean_pages([P("https://x.example/g", "Guide", md)])
    assert [b.text for b in cleaned[0].blocks if b.kind == "heading"] == ["# Guide", "## Install"]
    assert stats.orphan_headings == 2


def test_junk_and_in_page_duplicates():
    junk = ["We use cookies to improve your experience.", "© 2026 Example Inc.", "Back to top"]
    md = "\n\n".join([BODY, junk[0], BODY, *junk[1:]])
    cleaned, stats = clean_pages([P("https://x.example/a", "A", md)])
    assert cleaned[0].markdown == BODY
    assert stats.junk_blocks == 3 and stats.duplicate_blocks == 1


def test_thin_and_duplicate_pages():
    pages = [
        P("https://x.example/a", "A", BODY + " Unique text for A to pass the word limit easily, yes."),
        P(
            "https://x.example/a-copy",
            "A copy",
            BODY.upper() + " unique text for a to pass the word limit easily yes",
        ),
        P("https://x.example/thin", "Thin", "Just a few words."),
    ]
    cleaned, stats = clean_pages(pages)
    assert [p.url for p in cleaned] == ["https://x.example/a"]
    assert stats.duplicate_pages == 1 and stats.thin_pages == 1
    assert stats.pages_in == 3 and stats.pages_out == 1


def test_content_hash_is_stable_and_sensitive():
    first, _ = clean_pages(site(1))
    again, _ = clean_pages(site(1))
    changed, _ = clean_pages([P("https://x.example/0", "Page 0", f"# Page 0\n\n{BODY} Topic number 99.")])
    assert first[0].content_hash == again[0].content_hash != changed[0].content_hash
