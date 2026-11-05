from scoutai.clean import parse_blocks


def kinds(markdown: str) -> list[str]:
    return [b.kind for b in parse_blocks(markdown)]


def test_basic_block_types():
    md = """# Title ¶

Intro paragraph
that wraps a line.

- one
- two
1. three

| a | b |
|---|---|
| 1 | 2 |

```python
print("hi")

print("bye")
```
Text glued to the fence."""
    blocks = parse_blocks(md)
    assert [b.kind for b in blocks] == ["heading", "text", "list", "table", "code", "text"]
    assert blocks[0].text == "# Title" and blocks[0].level == 1 and blocks[0].heading_text == "Title"
    assert blocks[1].text == "Intro paragraph\nthat wraps a line."
    assert blocks[2].text == "- one\n- two\n1. three"
    assert blocks[4].text == '```python\nprint("hi")\n\nprint("bye")\n```'  # blank line inside code kept


def test_heading_closing_hashes_and_spacing():
    block = parse_blocks("##   Spaced   heading  ##")[0]
    assert block.text == "## Spaced heading" and block.level == 2


def test_unterminated_fence_is_closed():
    blocks = parse_blocks("```\ncode forever")
    assert blocks[0].kind == "code" and blocks[0].text == "```\ncode forever\n```"


def test_mid_line_fence_becomes_inline_code_and_does_not_swallow_page():
    md = "Run the ```uv\nsync``` command.\n```\nLater paragraph.\n\n## Next"
    blocks = parse_blocks(md)
    assert blocks[0].kind == "text"
    assert "`uv sync` command." in blocks[0].text.replace("``", "`") or "`uv" in blocks[0].text
    assert blocks[-1].kind == "heading"
    assert any("Later paragraph." in b.text and b.kind == "text" for b in blocks)


def test_balanced_inline_fences_are_downgraded():
    assert parse_blocks("Use ```pip``` here.")[0].text == "Use `pip` here."


def test_tilde_fences_and_mismatched_closers():
    blocks = parse_blocks("~~~\na\n```\nb\n~~~\nafter")
    assert blocks[0].text == "~~~\na\n```\nb\n~~~"
    assert blocks[1].text == "after"


def test_fingerprint_ignores_case_and_punctuation():
    a, b = parse_blocks("Hello, World!\n\nhello world")
    assert a.fingerprint == b.fingerprint == "hello world"
