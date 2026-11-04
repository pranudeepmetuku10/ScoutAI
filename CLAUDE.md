# ScoutAI

Local-first RAG app: crawl a website, build a knowledge base, chat with it using cited answers.
The roadmap and milestone status live in [PLAN.md](PLAN.md); read it first and tick milestones off as they land.

## Commands

- Install / sync deps: `uv sync`
- Tests: `uv run pytest`
- Lint + format check: `uv run ruff check . && uv run ruff format --check .`
- Auto-fix: `uv run ruff check --fix . && uv run ruff format .`
- Try a crawl: `uv run scoutai crawl <url> --max-pages 25 --depth 2 [--same-path] [--js off|auto|always] [--out f.json] [-v]`

## Conventions

- Code lives in `src/scoutai/`, one subpackage per pipeline stage (see PLAN.md layout).
- Every module gets unit tests in `tests/`; HTML test inputs go in `tests/fixtures/`. Tests must not hit the
  network: use fixtures or `httpx.MockTransport`.
- Type hints everywhere; keep functions small and pure where possible so stages are testable alone.
- Config via environment variables prefixed `SCOUTAI_`, read in one place (`scoutai/config.py`).
- Retrieval changes must be justified by the eval harness (`evals/`), not by eyeballing a few answers.
- This is an original implementation. Don't copy code from the reference BrowseAI project
  (`~/github_projects/BrowseAI_RAG`); reimplement ideas instead.

## Git

- Never commit or push without asking the user first, every time. Finish the work, show what changed, then ask.

## Safety

- The crawler must respect robots.txt, rate-limit per host, and refuse private/loopback addresses unless
  `SCOUTAI_ALLOW_PRIVATE_HOSTS=1`.
