"""Command line entry point: `scoutai crawl <url>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from scoutai.chunk import chunk_page
from scoutai.clean import clean_pages
from scoutai.config import get_settings
from scoutai.crawl import Crawler, CrawlError


async def _crawl(args: argparse.Namespace) -> int:
    async def emit(event: str, **data: object) -> None:
        if event == "page":
            flag = " [js]" if data["rendered"] else ""
            progress = f"[{data['count']:>3}/{data['max']}] d{data['depth']}"
            print(f"  {progress} {data['url']} ({data['words']} words){flag}")
        elif event == "skip" and args.verbose:
            print(f"  skip  {data['url']}: {data['reason']}")
        elif event == "log":
            print(f"  {data['level']}: {data['message']}")

    try:
        crawler = Crawler(
            args.url,
            max_pages=args.max_pages,
            max_depth=args.depth,
            same_path=args.same_path,
            js_mode=args.js,
            emit=emit,
        )
        started = time.perf_counter()
        result = await crawler.run()
    except CrawlError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    words = sum(p.words for p in result.pages)
    elapsed = time.perf_counter() - started
    print(f"\n{len(result.pages)} pages, {words:,} words, {len(result.skipped)} skipped in {elapsed:.1f}s")

    settings = get_settings()
    cleaned, stats = clean_pages(result.pages)
    chunks = [
        c
        for page in cleaned
        for c in chunk_page(page, size=settings.chunk_size, overlap=settings.chunk_overlap)
    ]
    removed = stats.boilerplate_blocks + stats.junk_blocks + stats.duplicate_blocks
    print(
        f"clean: {stats.pages_out}/{stats.pages_in} pages kept, "
        f"{stats.words_after:,}/{stats.words_before:,} words, {removed} blocks removed "
        f"({stats.boilerplate_blocks} boilerplate, {stats.junk_blocks} junk, "
        f"{stats.duplicate_blocks} duplicate), "
        f"{stats.thin_pages} thin, {stats.duplicate_pages} duplicate pages"
    )
    if chunks:
        sizes = sorted(len(c.text) for c in chunks)
        median = sizes[len(sizes) // 2]
        print(f"chunk: {len(chunks)} chunks, chars min/median/max {sizes[0]}/{median}/{sizes[-1]}")
    if args.verbose and stats.boilerplate_samples:
        print("boilerplate samples:", *(f"  {s!r}" for s in stats.boilerplate_samples), sep="\n")

    if args.out:
        output = {
            "crawl": asdict(result),
            "clean_stats": asdict(stats),
            "pages": [
                {"url": p.url, "title": p.title, "hash": p.content_hash, "markdown": p.markdown}
                for p in cleaned
            ],
            "chunks": [{**asdict(c), "breadcrumb": c.breadcrumb} for c in chunks],
        }
        args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False))
        print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scoutai")
    commands = parser.add_subparsers(dest="command", required=True)

    crawl = commands.add_parser("crawl", help="crawl a website and print what was collected")
    crawl.add_argument("url")
    crawl.add_argument("--max-pages", type=int, default=25)
    crawl.add_argument("--depth", type=int, default=2)
    crawl.add_argument("--same-path", action="store_true", help="stay under the start URL's directory")
    crawl.add_argument("--js", choices=["off", "auto", "always"], default="auto")
    crawl.add_argument("--out", type=Path, help="write pages and skips as JSON")
    crawl.add_argument("-v", "--verbose", action="store_true", help="also print skipped URLs")

    args = parser.parse_args(argv)
    return asyncio.run(_crawl(args))


if __name__ == "__main__":
    raise SystemExit(main())
