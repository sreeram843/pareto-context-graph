# Contributing to pareto-context-graph

Thanks for helping improve retrieval quality, docs, and tooling.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,tiktoken,policy,otel]"
```

Clone a T1 bench repo for integration tests:

```bash
make bench-setup-t1
```

## Before you open a PR

1. **Format & lint:** `ruff check . && ruff format --check .`
2. **Types:** `mypy src/pareto_context_graph`
3. **Tests:** `pytest -q`
4. **Eval gate** (when touching retrieval/packing):  
   `make eval-check REPOS='fastapi=bench/fastapi httpx=bench/httpx'`
5. **Agent A/B** (optional, when touching agent UX / MCP):  
   `make eval-agent-ab-check REPOS='fastapi=bench/fastapi httpx=bench/httpx'`

## CI test selection (PCG `affected`)

After `pareto-context-graph build` (or snapshot import), suggest tests for a PR diff:

```bash
git diff --name-only origin/main...HEAD | pareto-context-graph affected --stdin --quiet
```

Copy [`.github/workflows/pcg-affected.yml.example`](../.github/workflows/pcg-affected.yml.example) into your repo as `.github/workflows/pcg-affected.yml` and adapt the test runner.

Pre-merge MCP flow: use the `pre_merge_check` prompt (`detect_changes` → `affected` → `savings`).

## Project layout

- `src/pareto_context_graph/` — MCP server, graph build, eval harness
- `tests/` — unit and integration tests
- `tests/eval/golden/` — curated retrieval cases (do not auto-generate expected files)
- `docs/` — architecture, commands, benchmarks

## Code guidelines

- Prefer extending `taxonomy.py` for path/query classification instead of new ad-hoc regexes.
- Context pipeline logic belongs in `context_ranking.py` / `context_confidence.py`, not new branches in `server.py`.
- New optional dependencies go in `pyproject.toml` extras, not core `dependencies`.
- Golden eval changes need `make eval-baseline` and a note in the PR describing recall impact.

## Reporting issues

Include: repo size/profile, `context` request JSON (redact secrets), `doctor` output, and whether the graph was built from snapshot or cold build.
