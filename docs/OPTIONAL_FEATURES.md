# Optional features

Extras beyond the stdlib core. None are required for `build` + `context` on a typical repo.

---

## Embeddings

```bash
pareto-context-graph embed   # noop (default), OpenAI, or Ollama backend
```

Blended at **0.15** weight when a sidecar exists.

Backends: `DeterministicNoopBackend`, `OpenAIBackend`, `OllamaBackend`.

---

## Learned ranking

```bash
pip install -e '.[ranker]'   # LightGBM for LambdaMART
pareto-context-graph learn --ranker auto
```

Writes `weights.json` and optional `ranker.lgb.txt` from `feedback_*` events.
See [FEEDBACK.md](FEEDBACK.md).

---

## Hooks

Place Python in `.pareto-context-graph/hooks/`:

- `pre_context`, `post_context`, `post_build`, `post_update`

See [HOOKS.md](HOOKS.md).

---

## Snapshots & signing

Export/import `.pareto-context-graph` tarballs for fast onboarding on huge repos.
**Start here for kubernetes/linux:** [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md)

```bash
pareto-context-graph snapshot export ./graph.tar.gz
pareto-context-graph snapshot import ./graph.tar.gz
export PCG_SNAPSHOT_KEY='…'
export PCG_REQUIRE_SIGNED_SNAPSHOTS=1   # optional hard gate
# Optional Ed25519: PCG_ED25519_KEY + pip install cryptography
```

CI publishes signed kubernetes snapshots when `PCG_SNAPSHOT_KEY` is set.
See [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md).

---

## tree-sitter symbols (default when installed)

```bash
pip install -e '.[treesitter]'
pareto-context-graph build   # symbol index uses tree-sitter for .py / .go / TS
```

Tree-sitter is **on by default** when grammars are installed (`PCG_FEATURE_TREESITTER=1`).
Set `PCG_FEATURE_TREESITTER=0` to force regex-only symbol extraction.
`doctor` reports symbol index mode and warns when running regex fallback.

**Route edges** (FastAPI/Flask `@router.get`, `include_router`) are extracted automatically
when `STRUCTURAL_EDGES` is on (default).

---

## Context compression (built-in)

Query-aware **prune** after tier-3 packing — no extra package or MCP server.

```json
{"command": "context", "tier": 3, "compression": "prune", "query": "…", "files": ["…"]}
{"command": "retrieve", "content_hash": "<from context response>"}
```

Cache: `.pareto-context-graph/payload_cache/`. Eval: `--compress-stack`.

Full guide: [CONTEXT_COMPRESSION.md](CONTEXT_COMPRESSION.md)

---

## Tier-1 summary prune (SWE-Pruner-style)

Lossy **post-pack** filter for tier-1 `context` — drops rows whose path/summary
do not match query terms (seed files and retrieval hits are always kept).

```bash
export PCG_FEATURE_SUMMARY_PRUNE=1
pareto-context-graph context --tier 1 --query "APIRouter Depends" --files fastapi/routing.py
```

Response includes `summary_prune.dropped_count` when rows were removed.
Override per request: `"summary_prune": true` in the MCP `context` call.

---

## Tier-1 learned prune (feedback bias)

Optional **post-pack** filter for tier-1 `context` — drops tail rows whose
`prune_weights.json` bias is strongly negative (files agents often reject).

Auto-on when `prune_weights.json` exists (from `pareto-context-graph learn`).

```bash
export PCG_FEATURE_LEARNED_TIER1_PRUNE=1   # force before weights exist
pareto-context-graph context --tier 1 --query "OAuth2 JWT" --files fastapi/security/http.py
```

Response includes `learned_tier1_prune.dropped_count` when rows were removed.
Override per request: `"learned_tier1_prune": true` (or `false` to disable).

---

## Python API (no MCP)

```python
from pareto_context_graph.api import ParetoContextGraph

cg = ParetoContextGraph("/path/to/repo")
cg.build()
result = cg.context(files=["src/main.py"], query="add logging", tier=1)
```

---

## Docker

```bash
docker build -t pareto-context-graph:latest .
docker run --rm -i -v /path/to/repo:/workspace pareto-context-graph:latest
```

See `docker-compose.yml`.

---

## Install extras

```bash
pip install -e ".[tiktoken,igraph,ranker]"
```

| Extra | Purpose |
|-------|---------|
| `tiktoken` | Accurate token counts |
| `igraph` | Leiden communities |
| `ranker` | LightGBM LambdaMART |

---

## Development

```bash
pip install -e ".[dev]"
make test
make eval
make bench-huge
make help
```

Roadmap: [ROADMAP.md](ROADMAP.md)
