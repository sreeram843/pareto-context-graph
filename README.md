# code-graph-mcp

**Give AI coding assistants the right context for every prompt — from any repo.**

An MCP server that learns file relationships from git history and tells the AI exactly which files to read for any question, code change, or debugging session. Instead of the AI guessing what to read (and getting it wrong), it reads the files that provably matter.

**Zero external dependencies.** Python 3.10+ stdlib only.

---

## Why This Exists

Every AI prompt from a repo needs context. Without guidance, the AI either:
- Reads too much → hits token limits, costs money, hallucinates from noise
- Reads too little → misses critical dependencies, gives wrong answers
- **Reads the wrong files** → spends tokens on irrelevant code and still misses key dependencies

The primary problem isn't how many tokens are used — it's whether the AI reads the **right files**.

| | Without tool | With tool |
|---|---|---|
| Files in scope | all 6,142 | 15 co-change ranked |
| Selection method | keyword search / guessing | git history + imports + naming |
| Orientation cost | — | **450 tokens** (T1 map before reading) |
| Files actually read | ~25 guessed | ~4 targeted |
| Relevance guarantee | none | proven by commit history |

*Measured on `telapp` (Ruby/Rails, 6,142 git-tracked files). T1 = path summaries only. Full task with T3 escalation uses ~40K tokens — comparable to naive, but the right files.*

---

## How It Works

### Three signals combined

1. **Co-change history** (primary) — Files that changed together in git commits are coupled. `user_auth.rb` + `session_controller.rb` changed together 47 times? They're related.

2. **Import/require detection** — Regex-based static analysis catches `import`, `require`, `from`, `#include`, `use` across 7 language families (Python, Ruby, JS/TS, C/C++, Rust, Go, Perl).

3. **Naming conventions** — `login_controller.rb` ↔ `login_controller_spec.rb`, `auth_service.rb` ↔ `auth_controller.rb`. Finds test/spec/impl pairs automatically.

### Pipeline

```
git log (co-change pairs)  ─┐
                            ├──▶  Ranked file list  ──▶  Token budget filter  ──▶  AI gets only what matters
import/require detection   ─┤                                │
naming convention pairs    ─┘                                ▼
                                                    Query-aware ranking
                                                    (boost files matching your question)
```

### Key design choices

| Decision | Why |
|----------|-----|
| Down-weight large commits, cap at 250 files/commit | Preserve signal while preventing pathological commits from dominating |
| Skip commits with <2 files | No co-change pairs to extract |
| Down-weight noisy commit subjects (`merge`, `bump`, `format`, `lint`, etc.) | Reduces coupling noise from mechanical changes |
| `min_weight=2` default | Filters one-off coincidences |
| BFS depth=2 | Captures indirect coupling (A→B→C) without explosion |
| Token budget (50K default) | Never overflows AI context window |
| Zero dependencies | Deploy anywhere, no pip drama |

---

## Quick Start

```bash
# 1. Install the package
pip install -e /path/to/code-graph-mcp

# 2. Build the graph (run once per repo, ~30s for 5K commits)
cd /path/to/your-repo
code-graph-mcp build

# 3. Auto-configure your editor
code-graph-mcp install

# 4. Restart VS Code — done. AI now uses the tool on every prompt.
```

### Huge-Repo Bootstrap

```bash
# Fast start from a CI snapshot, then incremental update
code-graph-mcp build --from-snapshot <url-or-path-to-snapshot>

# Keep graph warm while serving MCP
code-graph-mcp serve --watch --interval 600
```

---

## The Primary Tool: `code_graph`

A single unified tool replaces the old 10-tool schema (~200 tokens schema overhead vs ~1000 before). The AI calls it with a `command` parameter.

### `context` command — what the AI calls on every prompt

```json
{
  "command": "context",
  "files": ["app/controllers/login_controller.rb"],
  "query": "add rate limiting to login",
  "tier": 1
}
```

### Progressive Tiers

| Tier | Returns | Use case | Tokens/file |
|------|---------|----------|-------------|
| **1** (default) | Path + 1-line summary | Orientation, triage | ~30 |
| **2** | Function/class signatures | Need API shape | ~50-200 |
| **3** | Relevant code chunks | Need implementation | full |

Start at tier 1. Escalate to tier 2/3 only for files you need to understand deeply.

### Delta Context (`already_have`)

On follow-up prompts, pass files you already have in context:

```json
{
  "command": "context",
  "files": ["login_controller.rb"],
  "query": "now add specs",
  "already_have": ["throttle.rb", "auth_service.rb"],
  "tier": 2
}
```

The tool skips those files, returning only new context. This eliminates redundant token usage across multi-turn conversations.

### Tier 1 Output

```json
{
  "seed_files": ["app/controllers/login_controller.rb"],
  "tier": 1,
  "context_files": [
    {"path": "app/middleware/throttle.rb", "summary": "class Throttle — request rate limiting", "tokens": 890},
    {"path": "app/services/auth_service.rb", "summary": "class AuthService — OAuth + sessions", "signal": "import", "tokens": 1200},
    {"path": "spec/controllers/login_controller_spec.rb", "summary": "RSpec.describe LoginController", "signal": "naming", "tokens": 650}
  ],
  "tokens_used": 90,
  "files_included": 3,
  "files_available": 18
}
```

### Tier 2 Output

```json
{
  "tier": 2,
  "context_files": [
    {"path": "app/middleware/throttle.rb", "signatures": ["class Throttle", "def call(env)", "def rate_limit_key(request)"], "tokens": 890}
  ]
}
```

### Tier 3 Output

```json
{
  "tier": 3,
  "context_files": [
    {"path": "app/middleware/throttle.rb", "chunks": [
      {"name": "def call", "lines": "12-28", "body": "def call(env)\n  key = rate_limit_key(env)..."}
    ], "tokens": 890}
  ]
}
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | string | *required* | `context`, `build`, `update`, `blast`, `neighbours`, `search`, `stats`, `hotspots`, `communities`, `savings` |
| `files` | string[] | *required for context* | Files the user is working in or asking about |
| `query` | string | `""` | User's question — used for ranking + keyword matching |
| `tier` | int | `1` | Detail level: 1=summaries, 2=signatures, 3=chunks |
| `already_have` | string[] | `[]` | Files to skip (already in AI context) |
| `token_budget` | int | `50000` | Max tokens to return |
| `min_weight` | int | `2` | Minimum co-change count |
| `max_depth` | int | `2` | BFS hops through graph |

### How ranking works

1. **Stage 1 candidates** — Co-change BFS (uses precomputed `top_neighbours` cache for speed) or Random Walk with Restart (for `huge` profile). Capped at `stage1_cap` results.
2. **Hybrid signals** — Import/require detection, naming-convention pairs (`foo.rb` ↔ `foo_spec.rb`), directory siblings, dbt ref resolution.
3. **TF-IDF keyword matching** — If `query` is provided, a lazy-built keyword index boosts files semantically related to the question.
4. **Query-aware path ranking** — Files whose paths match query terms get extra score.
5. **Hub suppression** — High-degree files (routers, configs) are penalized by `log2(2 + degree)` to prevent them from dominating results.
6. **Embedding score blending** — Optional cosine similarity from embeddings sidecar (0.15 weight).
7. **Learned weight boost** — Feedback-derived per-file scores from `weights.json` are added.
8. **MMR diversity selection** — Maximal Marginal Relevance re-ranks to reduce near-duplicate results (controlled by `mmr_lambda`).
9. **Iterative expansion** — For profiles with `iterations > 1`, top results become seeds for additional retrieval rounds.
10. Files added in rank order until `token_budget` is exhausted.

---

## All Commands

The server exposes a single `code_graph` tool with these commands:

| Command | Purpose |
|---------|---------|
| **`context`** | Primary. Returns ranked context files for any prompt (tiers + delta) |
| `build` | Build co-change graph from git history |
| `update` | Incremental update since last build (<2s) |
| `decay_sweep` | Apply recency decay and optional weak-edge pruning |
| `blast` | Files affected by current git diff |
| `savings` | Full repo vs blast radius token comparison |
| `neighbours` | Direct co-change relationships for a single file |
| `stats` | File/edge count, build metadata |
| `doctor` | Graph health diagnostics (hub stats + staleness metadata) |
| `hotspots` | Top coupled files — architectural hubs |
| `search` | FTS5 search over file paths |
| `communities` | Implicit module detection (file clusters) |
| `mark_used` | Mark files actually used by the assistant/user for feedback learning |

---

## CLI Reference

```bash
code-graph-mcp build [--commits N]        # Build graph (default: 5000 commits)
code-graph-mcp build [--since EXPR]       # Time-windowed history (for large repos)
code-graph-mcp build [--profile huge]      # Auto-tuned settings for repo size
code-graph-mcp build [--shards N]          # Shard-aware build entrypoint
code-graph-mcp build [--from-snapshot X]   # Restore snapshot + incremental update
code-graph-mcp query [--base BRANCH]      # Show blast radius + savings report
code-graph-mcp serve [--watch]              # Start MCP server (optional background updates)
code-graph-mcp install                     # Auto-configure VS Code + instructions
code-graph-mcp eval [--cases PATH --repo-map KEY=/abs/path --json]  # Run retrieval eval suite
code-graph-mcp decay-sweep                 # Recency decay + pruning maintenance
code-graph-mcp stats                       # JSON graph stats
code-graph-mcp doctor                      # Human-readable health report
code-graph-mcp snapshot export FILE        # Export .code-graph snapshot
code-graph-mcp snapshot import FILE        # Import .code-graph snapshot
code-graph-mcp learn                       # Fit feedback-derived ranking boosts
code-graph-mcp embed                       # Build optional embeddings sidecar
```

### `build`

Scans git log, extracts file pairs from each commit, stores weighted edges in SQLite.

```bash
code-graph-mcp build                    # Last 5000 commits
code-graph-mcp build --commits 10000    # More history = better accuracy
code-graph-mcp build --since "12 months ago"      # Recency window for huge repos
code-graph-mcp build --since "2025-01-01" --commits 50000  # Bound by date and commit cap
code-graph-mcp build --profile huge --shards 8    # Large-repo preset
```

### `decay-sweep`

Apply exponential recency decay to edges so recent code history has more influence:

```bash
code-graph-mcp decay-sweep --profile huge
code-graph-mcp decay-sweep --half-life-days 180 --prune-below 0.05
```

### `doctor` and `stats`

```bash
code-graph-mcp doctor
code-graph-mcp stats
```

`doctor` is for humans. `stats` is for automation and dashboards.

### `query`

Shows what the AI would read for your current branch changes.

```bash
code-graph-mcp query --base main        # Compare against main
code-graph-mcp query --base master --json  # Machine-readable output
code-graph-mcp query --min-weight 3 --depth 1  # Stricter filtering
```

### `serve`

Starts the MCP JSON-RPC 2.0 server on stdin/stdout. This is what your editor connects to.

```bash
code-graph-mcp serve
```

### `install`

Writes two files to your repo:

1. **`.vscode/mcp.json`** — MCP server configuration for VS Code/Copilot
2. **`.github/copilot-instructions.md`** — Instructions telling the AI to call `code_graph` with `command="context"` before every prompt

```bash
code-graph-mcp install
```

After install, restart your editor. The AI will automatically scope its context on every interaction.

---

## Editor Integration

### VS Code / GitHub Copilot

After `code-graph-mcp install`, your `.vscode/mcp.json` contains:

```json
{
  "servers": {
    "code-graph-mcp": {
      "command": "code-graph-mcp",
      "args": ["serve", "--repo", "/path/to/your/repo"],
      "type": "stdio"
    }
  }
}
```

The `.github/copilot-instructions.md` tells Copilot to:
1. Call `code_graph` with command="context" before answering any question
2. Start at tier 1 (summaries), escalate to tier 2/3 only if needed
3. Pass `already_have` on follow-up prompts to avoid redundant context

### Evaluation workflow

The CLI includes a retrieval-quality evaluator for regression checks.

```bash
# Run evaluation cases with explicit repo mappings
code-graph-mcp eval \
  --cases tests/eval/cases.json \
  --repo-map telapp=/abs/path/to/telapp \
  --repo-map deploy=/abs/path/to/deploy \
  --repo-map nova-transform=/abs/path/to/nova-transform

# JSON output for CI pipelines
code-graph-mcp eval --repo-map telapp=/abs/path/to/telapp --json

# Refresh golden snapshots under tests/eval/golden
code-graph-mcp eval --repo-map telapp=/abs/path/to/telapp --update-golden
```

Evaluation metrics reported:
- `recall@5`
- `MRR`
- `NDCG@10`
- `tokens_used`

### Snapshot workflow

```bash
# Export from CI or a warm machine
code-graph-mcp snapshot export ./graph-snapshot.tar.gz

# Import on a new machine
code-graph-mcp snapshot import ./graph-snapshot.tar.gz
```

### Feedback learning workflow

```bash
code-graph-mcp learn
```

This writes `.code-graph/weights.json` and boosts files that have high observed usage.

---

## Profiles

Profiles auto-tune build and query parameters based on repository size. If no `--profile` is specified, the CLI auto-detects based on commit count.

| Profile | Commits | Since | Shards | Expansion | Iterations | Half-life | MMR λ |
|---------|---------|-------|--------|-----------|------------|-----------|-------|
| **tiny** | 5K | — | 1 | BFS | 1 | 365d | 0.7 |
| **medium** | 20K | 24mo | 2 | BFS | 1 | 270d | 0.7 |
| **large** | 50K | 18mo | 4 | BFS | 1 | 220d | 0.65 |
| **huge** | 80K | 12mo | 8 | RWR | 2 | 180d | 0.6 |

Auto-detection thresholds (by `git rev-list --count HEAD`):
- \> 100K commits → `huge`
- \> 50K → `large`
- \> 10K → `medium`
- Otherwise → `tiny`

```bash
code-graph-mcp build --profile huge      # Explicit profile
code-graph-mcp build                     # Auto-detected from commit count
```

---

## Hooks

Place Python files in `.code-graph/hooks/` to extend the pipeline. Each file can export any of these functions:

| Hook | Signature | When |
|------|-----------|------|
| `pre_context(payload) → payload` | Modify context request before processing | Before context pipeline |
| `post_context(response) → response` | Modify/filter context response | After context pipeline |
| `post_build(result) → result` | React to graph build completion | After build |
| `post_update(result) → result` | React to incremental update | After update |

Built-in safety: all context responses pass through secret redaction (AWS keys, API keys) before being returned.

See [docs/HOOKS.md](docs/HOOKS.md) for examples including git post-commit auto-update hooks.

---

## Embeddings (optional)

Build an embeddings sidecar to improve ranking with semantic similarity. Three backends are available:

| Backend | Dependencies | Use case |
|---------|-------------|----------|
| `DeterministicNoopBackend` | None | Default — deterministic hash-based vectors |
| `OpenAIBackend` | `OPENAI_API_KEY` env var | Best quality, requires API access |
| `OllamaBackend` | Local Ollama server | Private/offline, good quality |

```bash
code-graph-mcp embed              # Build with noop backend (default)
```

Embedding scores are blended into ranking at 0.15 weight when available.

---

## Daemon / Watch Mode

For long-running editor sessions, `--watch` runs periodic incremental updates in the background:

```bash
code-graph-mcp serve --watch                  # Default: update every 600s
code-graph-mcp serve --watch --interval 300   # Update every 5 minutes
```

The watcher calls `incremental_update` and invalidates in-memory caches automatically.

---

### Cursor / Claude Desktop

Add to your MCP configuration:

```json
{
  "mcpServers": {
    "code-graph-mcp": {
      "command": "code-graph-mcp",
      "args": ["serve", "--repo", "/path/to/your/repo"]
    }
  }
}
```

---

## Architecture

```
src/code_graph_mcp/
├── server.py     # MCP JSON-RPC server — single tool dispatch, 6-phase context pipeline
├── blast.py      # BFS blast radius + import detection + naming pairs + summaries
├── chunks.py     # Chunk extraction, symbol index, TF-IDF keyword index
├── graph.py      # Git log parsing → co-change pair extraction → SQLite (sharded build)
├── store.py      # SQLite storage (files, edges, FTS5 search, communities, feedback)
├── tokens.py     # Token estimation (suffix-aware + bytes/token fallback) and savings
├── eval.py       # Retrieval-quality evaluation harness + golden snapshots
├── cli.py        # CLI entry point (build, query, serve, install, eval, decay-sweep, stats, doctor, snapshot, embed, learn)
├── profiles.py   # Size-based profile presets (tiny/medium/large/huge) with auto-detection
├── walk.py       # Random walk with restart (Personalized PageRank) over top_neighbours
├── daemon.py     # Background incremental updater (GraphWatcher) for serve --watch
├── hooks.py      # Repository-local hook loading (pre/post context, post build, post update) + secret redaction
├── embed.py      # Optional embeddings sidecar (DeterministicNoop, OpenAI, Ollama backends)
└── snapshot.py   # Export/import .code-graph snapshots (local or URL)
```

### `context` pipeline

```
Pre-context hooks     → run .code-graph/hooks/*.py pre_context()
Phase 1: Stage 1      → BFS or RWR expansion from seed files (cached top_neighbours)
Phase 2: Hybrid signals → import detection + naming pairs + keyword match + dir siblings
Phase 3: Filter       → remove deleted files, skip already_have, deduplicate
Phase 4: Scoring      → hub suppression + embedding blend + learned weights + query-path boost
Phase 5: MMR select   → diversity-aware re-ranking (mmr_lambda)
Phase 6: Token budget → stop when budget exhausted, tier-aware output
Phase 7: Tiered response → tier 1: summaries, tier 2: signatures, tier 3: chunks
Post-context hooks    → secret redaction + .code-graph/hooks/*.py post_context()
```

### Storage

- Graph stored in `.code-graph/graph.db` (SQLite, add to `.gitignore`)
- FTS5 index for fast file path search
- Supports incremental updates (only processes new commits since last build)

---

## Performance

| Operation | Time |
|-----------|------|
| Full build (5K commits) | ~30s |
| Incremental update | <2s |
| `context` response | <1s |
| `search` FTS5 lookup | <50ms |

### Tested repos

| Repo | Stack | Files (tracked) | Co-change edges | T1 orientation |
|------|-------|-----------------|-----------------|----------------|
| telapp | Ruby/Rails | 6,142 | 58,149 | 15 files / 450 tokens |
| deploy | Kubernetes/Helm | 10,106 | 136,888 | 98 files / 2,940 tokens |
| nova-transform | Python/dbt/SQL | 2,549 | 32,601 | 15 files / 450 tokens |
| ucp-availability-service | Java | 165 | 3,013 | 15 files / 450 tokens |

T1 orientation is the map step — summaries that tell the AI *which* files to escalate. Full task cost (with T3 content reads) is similar to naive file reading, but targeted at the right files.

---

## What the Tool Actually Does

1. **Reads the right files** — The AI queries the co-change graph from your seed file and gets the files most likely to matter — ones that provably changed together in git, share imports, or follow naming conventions. Same token cost as reading random files, but guaranteed relevant.

2. **T1 orientation before diving in** — 450 tokens of path summaries tells the AI *which* files to escalate before reading a single line of code. Without this map, the AI reads files hoping to find something useful.

3. **Chunk-level content at T3** — Tier 3 returns relevant functions/classes, not entire files. A 500-line file might yield 3 targeted chunks (60 lines total).

4. **Delta context genuinely saves on multi-turn conversations** — On follow-ups, `already_have` skips files already in context. In a 5-turn conversation this eliminates 4× redundant re-reads — a real and measurable saving.

5. **Single tool schema** — One tool (~200 tokens overhead) instead of ten (~1000 tokens). Saves overhead on every prompt.

### Without the tool (typical AI behavior)
```
User asks question
→ AI keyword-searches repo, reads ~25 files (guessed)
→ ~27K tokens spent, may still miss the key file
→ answer is incomplete or wrong
```

### With the tool
```
User asks question
→ AI calls code_graph(context, tier=1): map of 15 co-change ranked files — 450 tokens
→ AI escalates 4 targeted files to tier=3: full content — ~40K tokens
→ answer is accurate, right files every time

On follow-up (delta context):
→ AI passes already_have=["file1.rb", "file2.rb", ...]
→ tool skips those, returns only new context
→ saves ~30K tokens on every subsequent turn
```

---

## Requirements

- Python 3.10+
- Git CLI available in `PATH`
- Git repository with history
- No external dependencies (stdlib + sqlite3 only)

## Installation

```bash
# From source
pip install -e .

# The package provides the `code-graph-mcp` command
code-graph-mcp --version
```

## Docker Deployment

Build the image:

```bash
docker build -t code-graph-mcp:latest .
```

Run MCP server over stdio (mount your target repo at `/workspace`):

```bash
docker run --rm -i \
  -v /path/to/your-repo:/workspace \
  code-graph-mcp:latest
```

Run one-off commands inside the container:

```bash
docker run --rm \
  -v /path/to/your-repo:/workspace \
  code-graph-mcp:latest --repo /workspace build

docker run --rm \
  -v /path/to/your-repo:/workspace \
  code-graph-mcp:latest --repo /workspace query --base main
```

Use Docker Compose:

```bash
docker compose build
docker compose run --rm code-graph-mcp --repo /workspace build
docker compose up code-graph-mcp
```

### VS Code / Copilot MCP config using Docker

Use this in `.vscode/mcp.json` when you want the MCP server to run in Docker:

```json
{
  "servers": {
    "code-graph-mcp": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-v",
        "${workspaceFolder}:/workspace",
        "code-graph-mcp:latest"
      ],
      "type": "stdio"
    }
  }
}
```

The container already defaults to:

```bash
code-graph-mcp --repo /workspace serve --transport stdio
```

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
make test                    # or: python -m pytest -q

# Run evaluation suite
make eval

# Run benchmarks
make bench

# See all local + Docker Make targets
make help

# Docker workflow shortcuts
make docker-build
make docker-build-graph
make docker-query BASE=main
```

### Tests

23 tests covering builds, profiles, decay, ranking, snapshots, embeddings, hooks, feedback learning, daemon, and more. All tests use temporary repos and require no external services.

### Documentation

Additional docs in the `docs/` directory:

| File | Contents |
|------|----------|
| [HOOKS.md](docs/HOOKS.md) | Hook system reference with examples |
| [BENCHMARKS.md](docs/BENCHMARKS.md) | Recorded performance numbers |
| [CI_SNAPSHOTS.md](docs/CI_SNAPSHOTS.md) | CI snapshot bootstrap guide |
| [FEEDBACK.md](docs/FEEDBACK.md) | Feedback learning pipeline |
