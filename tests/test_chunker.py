from scoutai.chunk import chunk_page, split_block
from scoutai.clean import Block, CleanedPage, parse_blocks

SENTENCE = "Each sentence in this paragraph is roughly sixty characters long. "


def page(markdown: str, title: str = "Guide") -> CleanedPage:
    return CleanedPage(
        url="https://x.example/g", title=title, blocks=parse_blocks(markdown), content_hash="h"
    )


def test_sections_start_new_chunks_with_breadcrumbs():
    md = f"# Guide\n\n{SENTENCE * 4}\n\n## Install\n\n{SENTENCE * 4}\n\n### On macOS\n\n{SENTENCE * 4}"
    chunks = chunk_page(page(md))
    assert [c.section for c in chunks] == [("Guide",), ("Guide", "Install"), ("Guide", "Install", "On macOS")]
    assert chunks[2].breadcrumb == "Guide › Install › On macOS"  # H1 matching the title isn't repeated
    assert chunks[1].text.startswith("## Install\n\n")
    assert chunks[2].embedding_text.startswith("Guide › Install › On macOS\n\n### On macOS")
    assert [c.ordinal for c in chunks] == [0, 1, 2]


def test_heading_stack_pops_to_sibling_level():
    md = f"## A\n\n{SENTENCE * 4}\n\n### A1\n\n{SENTENCE * 4}\n\n## B\n\n{SENTENCE * 4}"
    assert [c.section for c in chunk_page(page(md))] == [("A",), ("A", "A1"), ("B",)]


def test_small_sections_merge_forward():
    md = "## Tiny\n\nShort.\n\n## Also tiny\n\nAlso short.\n\n## Big\n\n" + SENTENCE * 4
    chunks = chunk_page(page(md))
    assert len(chunks) == 1
    assert "## Tiny" in chunks[0].text and "## Big" in chunks[0].text


def test_long_prose_splits_on_sentences_with_overlap():
    chunks = chunk_page(page(f"## Long\n\n{SENTENCE * 40}"), size=500, overlap=150)
    assert len(chunks) > 3
    for c in chunks:
        assert len(c.text) <= 500 + len("## Long\n\n")
        assert c.section == ("Long",)
    assert chunks[1].text.startswith(SENTENCE.strip())  # overlap: previous chunk's last sentences
    assert chunks[0].text.endswith(".")


def test_no_overlap_across_sections():
    other = "Section B talks about something else entirely, at some length. "
    md = f"## A\n\n{SENTENCE * 12}\n\n## B\n\n{other * 12}"
    chunks = chunk_page(page(md), size=400, overlap=150, min_chars=200)
    b_chunks = [c for c in chunks if c.section == ("B",)]
    assert b_chunks and b_chunks[0].text.startswith("## B")
    assert not any(SENTENCE.strip() in c.text for c in b_chunks)
    assert any(c.text.startswith(other.strip()) for c in b_chunks[1:])  # overlap within B still works


def test_heading_only_pages_emit_nothing():
    blocks = [Block("heading", "# Only", 1), Block("heading", "## Headings", 2)]
    assert chunk_page(CleanedPage("u", "t", blocks, "h")) == []


def test_split_code_repeats_fences():
    code = Block("code", "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(100)) + "\n```")
    parts = split_block(code, 200)
    assert len(parts) > 1
    assert all(p.startswith("```python\n") and p.endswith("\n```") and len(p) <= 200 for p in parts)
    assert "line_99 = 99" in parts[-1]


def test_split_table_repeats_header():
    rows = "\n".join(f"| row {i} | value {i} |" for i in range(40))
    table = Block("table", "| name | value |\n|---|---|\n" + rows)
    parts = split_block(table, 150)
    assert len(parts) > 1
    assert all(p.startswith("| name | value |\n|---|---|\n") for p in parts)


def test_split_list_by_items_and_hard_wrap_giant_sentence():
    items = Block("list", "\n".join(f"- item number {i}" for i in range(50)))
    assert all(len(p) <= 100 and p.startswith("- ") for p in split_block(items, 100))
    giant = Block("text", "word " * 500)
    assert all(len(p) <= 120 for p in split_block(giant, 120))


def test_tiny_closing_section_merges_into_previous_chunk():
    md = f"## Main\n\n{SENTENCE * 5}\n\n## Next steps\n\nSee the guides."
    chunks = chunk_page(page(md), size=1000, min_chars=200)
    assert len(chunks) == 1
    assert chunks[0].section == ("Main",) and chunks[0].text.endswith("See the guides.")


def test_tiny_chunk_not_merged_when_it_would_exceed_size():
    md = f"## Main\n\n{SENTENCE * 5}\n\n## Next steps\n\nSee the guides."
    chunks = chunk_page(page(md), size=340, overlap=0, min_chars=200)
    assert chunks[-1].section == ("Next steps",)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
