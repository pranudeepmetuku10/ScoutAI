# ScoutAI

Chat with any website. ScoutAI crawls a site, builds a local knowledge base, and answers questions
with citations to the pages they came from.

> **Status:** early development. See [PLAN.md](PLAN.md) for the roadmap.

## Planned features

- Polite same-site crawler (robots.txt, sitemaps, rate limiting, optional JavaScript rendering)
- Cross-page boilerplate removal and heading-aware chunking
- Hybrid retrieval: BM25 + dense vectors, fused with RRF and reranked by a cross-encoder
- Streaming answers with verified citations, from Ollama, OpenAI-compatible APIs or Claude
- Built-in eval harness (recall@k, MRR, faithfulness)

## Try the crawler

```bash
uv sync
uv run scoutai crawl https://docs.astral.sh/uv/ --same-path --max-pages 50 --depth 3 --out pages.json
```

For JavaScript-heavy sites, install the optional renderer once:

```bash
uv sync --extra js
uv run playwright install chromium
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check .
```

## Acknowledgements

ScoutAI is an independent implementation.

## License

MIT
