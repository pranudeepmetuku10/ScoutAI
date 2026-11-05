"""Heading-aware chunking.

Chunks follow a page's section structure: a heading starts a new chunk once the current one has real
content, and small sections merge forward so we don't emit fragments. Oversized blocks are split along
their natural seams: sentences for prose, items for lists, rows for tables (header repeated), lines for
code (fences repeated). Every chunk remembers its breadcrumb, which is embedded with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scoutai.clean import Block, CleanedPage

SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[`*])")
CRUMB_SEPARATOR = " › "


@dataclass(frozen=True)
class Chunk:
    url: str
    title: str
    section: tuple[str, ...]  # heading path, outermost first
    text: str
    ordinal: int  # position within the page

    @property
    def breadcrumb(self) -> str:
        crumbs = [self.title] if self.title else []
        for heading in self.section:
            if not crumbs or heading.lower() != crumbs[-1].lower():  # H1 often repeats the title
                crumbs.append(heading)
        return CRUMB_SEPARATOR.join(crumbs)

    @property
    def embedding_text(self) -> str:
        return f"{self.breadcrumb}\n\n{self.text}" if self.breadcrumb else self.text


def _hard_wrap(text: str, size: int) -> list[str]:
    """Split text with no usable seams at word boundaries (or mid-word, as a last resort)."""
    parts: list[str] = []
    while len(text) > size:
        cut = text.rfind(" ", 0, size + 1)
        cut = cut if cut > size // 2 else size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def _pack(units: list[str], size: int, joiner: str, *, prefix: str = "", suffix: str = "") -> list[str]:
    """Greedily pack units into strings of at most `size` chars, each wrapped in prefix/suffix."""
    budget = max(size - len(prefix) - len(suffix), 1)
    parts: list[str] = []
    current: list[str] = []
    length = 0
    for unit in units:
        pieces = _hard_wrap(unit, budget) if len(unit) > budget else [unit]
        for piece in pieces:
            extra = len(piece) + (len(joiner) if current else 0)
            if current and length + extra > budget:
                parts.append(prefix + joiner.join(current) + suffix)
                current, length = [], 0
                extra = len(piece)
            current.append(piece)
            length += extra
    if current:
        parts.append(prefix + joiner.join(current) + suffix)
    return parts


def split_block(block: Block, size: int) -> list[str]:
    if len(block.text) <= size:
        return [block.text]
    lines = block.text.splitlines()
    if block.kind == "code":
        return _pack(lines[1:-1], size, "\n", prefix=lines[0] + "\n", suffix="\n" + lines[-1])
    if block.kind == "table":
        has_header = len(lines) > 1 and re.fullmatch(r"\|[\s:|-]+\|", lines[1])
        header = "\n".join(lines[:2]) + "\n" if has_header else ""
        return _pack(lines[2:] if has_header else lines, size, "\n", prefix=header)
    if block.kind == "list":
        return _pack(lines, size, "\n")
    return _pack(SENTENCE_BREAK.split(block.text), size, " ")


def _tail(text: str, overlap: int) -> str:
    """The last whole sentences of `text`, at most `overlap` chars."""
    out = ""
    for sentence in reversed(SENTENCE_BREAK.split(text)):
        candidate = f"{sentence} {out}".strip()
        if len(candidate) > overlap:
            break
        out = candidate
    return out


def chunk_page(
    page: CleanedPage, *, size: int = 1000, overlap: int = 150, min_chars: int = 200
) -> list[Chunk]:
    """`size` is a soft cap: a chunk may exceed it by the headings it starts with."""
    chunks: list[Chunk] = []
    path: list[tuple[int, str]] = []  # (level, heading) stack
    parts: list[str] = []  # pieces of the chunk being built
    content_chars = 0  # chars in `parts` excluding headings and carried overlap
    carried = False  # parts[0] is overlap from the previous chunk
    trailing_headings = 0  # headings at the end of `parts` with no content after them yet
    last_was_prose = False
    start_path: tuple[str, ...] = ()

    def current_path() -> tuple[str, ...]:
        return tuple(heading for _, heading in path)

    def flush(carry: str = "") -> None:
        nonlocal parts, content_chars, carried, start_path
        if content_chars:
            text = "\n\n".join(parts)
            chunks.append(Chunk(page.url, page.title, start_path, text, len(chunks)))
        parts = [carry] if carry else []
        carried = bool(carry)
        content_chars = 0
        start_path = current_path()

    for block in page.blocks:
        if block.kind == "heading":
            if content_chars >= min_chars:
                flush()
            elif not content_chars and carried:
                parts.pop(0)  # don't drag the previous section's overlap into a new section
                carried = False
            path[:] = [(lvl, h) for lvl, h in path if lvl < block.level] + [(block.level, block.heading_text)]
            if not content_chars:
                start_path = current_path()  # the chunk takes the path of its deepest leading heading
            parts.append(block.text)
            trailing_headings += 1
            last_was_prose = False
            continue

        # Prose pieces leave room for the overlap a following chunk will carry, so chunks stay within size.
        limit = max(size - overlap, size // 2) if block.kind == "text" else size
        for piece in split_block(block, limit):
            if content_chars and sum(len(p) + 2 for p in parts) + len(piece) > size:
                if trailing_headings:
                    # Headings at the end belong to the chunk they introduce, not the one being closed.
                    headings = parts[-trailing_headings:]
                    del parts[-trailing_headings:]
                    flush()
                    parts = headings
                else:
                    last = parts[-1]
                    carry = _tail(last, overlap) if last_was_prose and overlap > 0 else ""
                    flush(carry if carry != last else "")
            parts.append(piece)
            trailing_headings = 0
            content_chars += len(piece)
            last_was_prose = block.kind == "text"
    flush()
    return _merge_small(chunks, size, min_chars)


def _merge_small(chunks: list[Chunk], size: int, min_chars: int) -> list[Chunk]:
    """Fold a tiny chunk into the previous one when both fit: same section, or the page's closing section
    ("Next steps", "Learn more") that would otherwise be a near-useless fragment."""
    merged: list[Chunk] = []
    for i, chunk in enumerate(chunks):
        prev = merged[-1] if merged else None
        is_last = i == len(chunks) - 1
        if (
            prev is not None
            and len(chunk.text) < min_chars
            and len(prev.text) + 2 + len(chunk.text) <= size
            and (chunk.section == prev.section or is_last)
        ):
            merged[-1] = Chunk(
                prev.url, prev.title, prev.section, f"{prev.text}\n\n{chunk.text}", prev.ordinal
            )
        else:
            merged.append(Chunk(chunk.url, chunk.title, chunk.section, chunk.text, len(merged)))
    return merged
