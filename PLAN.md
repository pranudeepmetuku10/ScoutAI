# ScoutAI — Project Plan

**Goal:** a local-first "chat with any website" app whose retrieval quality is *measured*, not guessed.
The headline result to aim for: *"hybrid retrieval + reranking lifts recall@5 from X% to Y% on a 150-question eval set."*

Inspired by the BrowseAI project in [Ramakm/RAG](https://github.com/Ramakm/RAG) (MIT). ScoutAI is a fresh
implementation; ideas are reimplemented, not copied.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Tooling | Python 3.12+, `uv`, `ruff`, `pytest`, GitHub Actions | Fast setup, CI from day one |
| Crawl / extract | `httpx` + `trafilatura` + optional Playwright | Proven approach; parse each page once |
| Storage | One SQLite file per knowledge base: pages, chunks, jobs, **FTS5** (BM25) and **sqlite-vec** (vectors) | Real keyword search, persistent jobs, one portable file |
| Embeddings | `BAAI/bge-small-en-v1.5` (configurable) | Better than MiniLM at the same size |
| Reranker | `BAAI/bge-reranker-base` cross-encoder | Largest quality gain per unit of effort |
| LLM | Thin provider interface: Ollama, OpenAI-compatible APIs, Anthropic | No lock-in |
| API | FastAPI + Server-Sent Events | Streams pipeline progress and answers |
| Frontend | React + Vite + TypeScript (`web/`) | Room to grow |
| Packaging | Docker Compose (app + Ollama) | One-command demo |

## Layout

```
ScoutAI/
├── src/scoutai/
│   ├── crawl/      # fetcher, robots, sitemap (incl. sitemap index + robots "Sitemap:"), url utils, JS renderer
│   ├── extract/    # HTML → Markdown, title, links (single parse per page)
│   ├── clean/      # cross-page boilerplate, junk lines, duplicate blocks/pages
│   ├── chunk/      # heading-aware chunks with breadcrumbs
│   ├── index/      # SQLite schema, FTS5, sqlite-vec, embedder
│   ├── retrieve/   # BM25 + dense → RRF → rerank → per-page diversity cap
│   ├── generate/   # providers, prompts, query rewrite, citation check
│   ├── jobs/       # persistent job queue, cancel, progress events
│   └── api/        # FastAPI routes
├── web/            # React frontend
├── evals/          # golden datasets, runner, reports
├── tests/          # unit tests + HTML fixtures
└── docker-compose.yml
```

## Milestones

Each milestone ends in a commit that works and is tested.

- [x] **M0: Skeleton.** uv project, ruff, pytest, CI, license, README, PLAN, CLAUDE.md.
- [ ] **M1: Crawler + extractor.** BFS same-site crawl, robots.txt + crawl-delay, sitemap index support,
  URL normalization, redirect dedupe, per-host rate limit, optional Playwright with page concurrency limit.
  Block private/loopback IPs by default (SSRF guard).
  *Done when:* fixture-HTML tests pass and a 50-page site crawls reproducibly.
- [ ] **M2: Clean + chunk.** Cross-page boilerplate threshold, junk patterns, code-fence repair,
  heading-aware chunks with breadcrumbs, per-page content hash (for incremental re-crawl).
  *Done when:* unit tests cover boilerplate removal and chunk boundaries.
- [ ] **M3: Eval harness (before any tuning).** 3 sites (docs, marketing, blog) × ~50 questions, each with
  expected source URL(s) and a reference answer. Runner reports recall@k and MRR; later faithfulness and
  citation accuracy via LLM judge.
  *Done when:* `make eval` prints a metrics table and writes a JSON report.
- [ ] **M4: Index + retrieval.** SQLite schema, FTS5, sqlite-vec. BM25 top-50 + dense top-50 → reciprocal
  rank fusion → cross-encoder rerank → per-page cap. Record the embedding model per KB and refuse to query
  a KB built with a different model.
  *Done when:* README has an eval table comparing dense-only vs hybrid vs hybrid + rerank.
- [ ] **M5: Generation.** Provider interface, LLM query rewrite for follow-ups, numbered-citation prompt,
  post-hoc citation check (flag `[n]` that point at nothing), extractive fallback when no LLM is reachable.
  *Done when:* faithfulness is in the eval report.
- [ ] **M6: API + jobs.** Jobs persisted in SQLite, SSE progress with replay, cancel endpoint, incremental
  re-crawl (skip pages whose ETag/hash is unchanged), list/delete KBs.
  *Done when:* restarting the server mid-crawl doesn't lose job state.
- [ ] **M7: Frontend.** URL + crawl settings, live pipeline view, streaming chat with clickable citations
  and source cards, page browser, model picker.
- [ ] **M8: Ship.** Docker Compose, demo GIF, README with architecture diagram and eval results, design notes.

## Stretch

- MCP server exposing each knowledge base as a tool for agents.
- PDF / document ingestion.
- Multi-site knowledge bases and scheduled re-crawls.
- Agentic mode: crawl more pages when retrieval confidence is low.
