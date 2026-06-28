# pareto-context-graph

**Give AI coding assistants the right context for every prompt — from any repo.**

An MCP server that learns file relationships from git history and returns a ranked,
token-budgeted list of files for any question, change, or debug session. Instead of
guessing what to read, the agent gets the files that provably matter — then escalates
detail only where needed.

- **Right files first** — co-change graph + query fusion + feedback, not keyword luck.
- **Honest budgets** — a tier-1 map (~30 tokens/file) before opening anything; escalate
  to signatures or full chunks only when needed.
- **No external service** — Python 3.10+ stdlib (SQLite + git CLI). Embeddings, a learned
  ranker, and OpenTelemetry are optional extras.

---

## Install

```bash
pip install "pareto-context-graph[tiktoken]"   # tiktoken = accurate token budgets
```

## Use it (Cursor / any MCP client)

```bash
cd /path/to/your-repo
pareto-context-graph init --platform cursor    # build graph + install MCP config
# restart Cursor
```

After commits, refresh the graph with `pareto-context-graph sync` (or run
`serve --watch` to keep it live). Full setup and other editors:
[docs/QUICKSTART.md](docs/QUICKSTART.md).

Manual MCP config:

```json
{
  "mcpServers": {
    "pareto-context-graph": {
      "command": "pareto-context-graph",
      "args": ["serve", "--repo", "/absolute/path/to/your-repo"]
    }
  }
}
```

**Huge repos (kubernetes, linux):** import a signed
[CI/team snapshot](docs/CI_SNAPSHOTS.md) instead of a multi-hour cold build.

---

## How it works

The agent calls one tool — `context` (alias `explore`) — with a question and/or seed
files. The server:

1. **Selects** candidate files from the git co-change graph, query fusion (path /
   symbol / BM25 / embeddings), and import edges.
2. **Ranks** them by co-change strength, locality, and learned feedback, suppressing
   hubs and near-duplicates.
3. **Packs** the top files into a token budget as a tier-1 map, escalating to tier-2
   signatures or tier-3 chunks on request.

Architecture, pipeline phases, and storage: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Documentation

| Topic | Doc |
|-------|-----|
| Quick start — install, build, editors | [docs/QUICKSTART.md](docs/QUICKSTART.md) |
| Architecture — pipeline, modules, storage | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Commands & CLI — `context` params, all commands | [docs/COMMANDS.md](docs/COMMANDS.md) |
| Optional features — embeddings, ranker, hooks, compression, Docker | [docs/OPTIONAL_FEATURES.md](docs/OPTIONAL_FEATURES.md) |
| Benchmarks — eval gates, build speed, agent A/B | [docs/BENCHMARKS.md](docs/BENCHMARKS.md) |
| Huge-repo snapshots | [docs/CI_SNAPSHOTS.md](docs/CI_SNAPSHOTS.md) |
| Feedback learning loop | [docs/FEEDBACK.md](docs/FEEDBACK.md) |
| Tier-3 compression | [docs/CONTEXT_COMPRESSION.md](docs/CONTEXT_COMPRESSION.md) |
| Roadmap — open work | [docs/ROADMAP.md](docs/ROADMAP.md) |

Design note: structural edges, Leiden communities, and the install flow were informed by
[code-review-graph](https://github.com/tirth8205/code-review-graph) (patterns only — not a
dependency). `fastapi`/`httpx` are eval benchmark repos, not runtime dependencies.
