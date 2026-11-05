"""Cross-page cleaning: site-wide boilerplate, junk lines, duplicate blocks and duplicate pages.

Main-content extraction sees one page at a time, so text repeated on every page (a newsletter pitch, a
"related posts" list, a legal disclaimer) can survive it. Comparing all pages of a site exposes it.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from scoutai.clean.blocks import Block, parse_blocks, render_blocks

JUNK = re.compile(
    r"accept (all )?cookies|cookie (policy|settings|preferences)|we use cookies|this (web)?site uses cookies|"
    r"all rights reserved|skip to (main )?content|back to top|subscribe to (our )?newsletter|"
    r"sign up for (our )?newsletter|share (this|on) (post|page|article|twitter|facebook|linkedin|x)\b|"
    r"^(copyright|©)|was this (page|article) helpful|enable javascript|javascript is (disabled|required)|"
    r"^(previous|next)( (page|post|article))?$|edit (this page )?on github",
    re.IGNORECASE,
)
JUNK_MAX_CHARS = 200


class SourcePage(Protocol):
    url: str
    title: str
    markdown: str


@dataclass
class CleanedPage:
    url: str
    title: str
    blocks: list[Block]
    content_hash: str  # changes only when the cleaned text changes; used for incremental re-crawls

    @property
    def markdown(self) -> str:
        return render_blocks(self.blocks)

    @property
    def words(self) -> int:
        return sum(b.words for b in self.blocks)


@dataclass
class CleanStats:
    pages_in: int = 0
    pages_out: int = 0
    words_before: int = 0
    words_after: int = 0
    boilerplate_blocks: int = 0
    junk_blocks: int = 0
    duplicate_blocks: int = 0
    orphan_headings: int = 0
    thin_pages: int = 0
    duplicate_pages: int = 0
    boilerplate_samples: list[str] = field(default_factory=list)  # a few removed snippets, for debugging


def content_hash(blocks: Sequence[Block]) -> str:
    return hashlib.sha256("\n".join(b.fingerprint for b in blocks).encode()).hexdigest()


def _sample(stats: CleanStats, text: str) -> None:
    snippet = text[:80]
    if len(stats.boilerplate_samples) < 10 and snippet not in stats.boilerplate_samples:
        stats.boilerplate_samples.append(snippet)


def _units(block: Block) -> list[str]:
    """Fingerprints compared across pages. List items count individually: nav menus repeat item by item."""
    if block.kind == "list":
        return [Block("text", line).fingerprint for line in block.text.splitlines()]
    return [block.fingerprint]


def _drop_orphan_headings(blocks: list[Block]) -> tuple[list[Block], int]:
    """Remove headings left with no content beneath them (the next kept block is a same-or-higher heading)."""
    kept: list[Block] = []
    dropped = 0
    for block in reversed(blocks):
        if block.kind == "heading":
            following = kept[-1] if kept else None
            if following is None or (following.kind == "heading" and following.level <= block.level):
                dropped += 1
                continue
        kept.append(block)
    kept.reverse()
    return kept, dropped


def clean_pages(
    pages: Iterable[SourcePage],
    *,
    boilerplate_ratio: float = 0.5,
    min_pages_for_boilerplate: int = 4,
    min_words: int = 25,
) -> tuple[list[CleanedPage], CleanStats]:
    pages = list(pages)
    parsed = [parse_blocks(p.markdown) for p in pages]
    stats = CleanStats(pages_in=len(pages))

    # On how many distinct pages does each unit appear? Too few pages means we can't judge.
    frequency: Counter[str] = Counter()
    for blocks in parsed:
        frequency.update({unit for b in blocks if b.kind != "heading" for unit in _units(b)})
    n = len(pages)
    threshold = max(3, math.ceil(n * boilerplate_ratio)) if n >= min_pages_for_boilerplate else n + 1

    def is_boilerplate(unit: str) -> bool:
        return frequency[unit] >= threshold

    def is_junk(text: str) -> bool:
        return len(text) <= JUNK_MAX_CHARS and bool(JUNK.search(text.strip("-*+ ")))

    cleaned: list[CleanedPage] = []
    seen_hashes: set[str] = set()
    for page, blocks in zip(pages, parsed, strict=True):
        stats.words_before += sum(b.words for b in blocks)
        kept: list[Block] = []
        seen_units: set[str] = set()
        for block in blocks:
            if block.kind == "heading":
                kept.append(block)  # headings carry structure; orphans are dropped below
                continue
            if block.kind == "list":
                items = []
                for line in block.text.splitlines():
                    unit = Block("text", line).fingerprint
                    if is_boilerplate(unit):
                        stats.boilerplate_blocks += 1
                        _sample(stats, line)
                    elif is_junk(line):
                        stats.junk_blocks += 1
                    else:
                        items.append(line)
                if items:
                    kept.append(Block("list", "\n".join(items)))
                continue

            unit = block.fingerprint
            if not unit:
                continue
            if unit in seen_units:
                stats.duplicate_blocks += 1
            elif is_boilerplate(unit):
                stats.boilerplate_blocks += 1
                _sample(stats, block.text)
            elif block.kind != "code" and is_junk(block.text):
                stats.junk_blocks += 1
            else:
                seen_units.add(unit)
                kept.append(block)

        kept, orphans = _drop_orphan_headings(kept)
        stats.orphan_headings += orphans
        words = sum(b.words for b in kept)
        if words < min_words:
            stats.thin_pages += 1
            continue
        digest = content_hash(kept)
        if digest in seen_hashes:
            stats.duplicate_pages += 1
            continue
        seen_hashes.add(digest)
        stats.words_after += words
        cleaned.append(CleanedPage(url=page.url, title=page.title, blocks=kept, content_hash=digest))

    stats.pages_out = len(cleaned)
    return cleaned, stats
