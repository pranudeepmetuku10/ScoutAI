"""Split extracted Markdown into typed blocks: headings, paragraphs, lists, tables and fenced code.

Extraction output is not always well-formed. Code fences can be glued to the next paragraph with no blank
line, and inline `<code>` sometimes comes out as ``` in the middle of a sentence. An unpaired mid-line
fence would otherwise open a "code block" that swallows the rest of the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

BlockKind = Literal["heading", "text", "list", "table", "code"]

FENCE = re.compile(r"^(`{3,}|~{3,})([\w+#.-]*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)(\s+#+)?$")
LIST_ITEM = re.compile(r"^([-*+]|\d{1,3}[.)])\s+\S")
TABLE_ROW = re.compile(r"^\|.*\|$")
TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
INVISIBLE = str.maketrans("", "", "¶​‌‍⁠﻿")  # pilcrow anchors, zero-widths


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # heading level, 1-6

    @property
    def fingerprint(self) -> str:
        """Case-, punctuation- and whitespace-insensitive identity, for spotting repeats."""
        return re.sub(r"\W+", " ", self.text.lower()).strip()

    @property
    def heading_text(self) -> str:
        return self.text.lstrip("#").strip() if self.kind == "heading" else ""

    @property
    def words(self) -> int:
        return len(self.text.split())


def _collapse(line: str) -> str:
    return re.sub(r"[ \t]+", " ", line).strip()


def parse_blocks(markdown: str) -> list[Block]:
    blocks: list[Block] = []
    buf: list[str] = []
    buf_kind: BlockKind = "text"
    fence: str | None = None  # closing marker of the open fenced block
    fence_open = ""
    code: list[str] = []
    inline_span: str | None = None  # text of a mid-line ``` span still waiting for its closing fence

    def flush() -> None:
        if buf:
            blocks.append(Block(buf_kind, "\n".join(buf)))
            buf.clear()

    def close_code() -> None:
        body = "\n".join(code).strip("\n")
        if body.strip():
            blocks.append(Block("code", f"{fence_open}\n{body}\n{fence}"))
        code.clear()

    for raw in markdown.splitlines():
        line = raw.translate(INVISIBLE)
        stripped = line.strip()

        if fence is not None:
            closing = FENCE.match(stripped)
            # CommonMark: same character, at least as long as the opener, no info string.
            if closing and closing[1][0] == fence[0] and len(closing[1]) >= len(fence) and not closing[2]:
                close_code()
                fence = None
            else:
                code.append(line.rstrip())
            continue

        if inline_span is not None:
            if FENCE.match(stripped):
                buf.append(_collapse(inline_span + "`"))
                inline_span = None
            elif stripped:
                inline_span += " " + stripped
            continue

        if match := FENCE.match(stripped):
            flush()
            fence, fence_open = match[1], stripped
            continue
        if not stripped:
            flush()
            continue
        if stripped.count("```") % 2 == 1:
            # Mid-line fence: inline code that runs onto following lines until a bare fence closes it.
            if buf_kind != "text":
                flush()
            buf_kind = "text"
            inline_span = stripped.replace("```", "`")
            continue
        stripped = stripped.replace("```", "`")

        if match := HEADING.match(stripped):
            flush()
            blocks.append(Block("heading", f"{match[1]} {_collapse(match[2])}", level=len(match[1])))
            continue

        kind: BlockKind = (
            "list" if LIST_ITEM.match(stripped) else "table" if TABLE_ROW.match(stripped) else "text"
        )
        if buf and kind != buf_kind:
            flush()
        buf_kind = kind
        buf.append(stripped if kind == "table" else _collapse(stripped))

    if fence is not None:
        close_code()
    if inline_span is not None:
        buf.append(_collapse(inline_span))
    flush()
    return blocks


def render_blocks(blocks: list[Block]) -> str:
    return "\n\n".join(b.text for b in blocks)
