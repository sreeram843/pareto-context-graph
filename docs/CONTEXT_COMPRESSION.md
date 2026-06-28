# Context compression (in-house)

Query-aware payload pruning after graph retrieval — built into the MCP server.

**North star:** same `recall@5`, fewer tier-3 tokens, verbatim restore via `content_hash`.

See also: [COMMANDS.md](COMMANDS.md) · [ARCHITECTURE.md](ARCHITECTURE.md).

---

## When to use

| Step | Command | Purpose |
|------|---------|---------|
| 1 | `context` tier 1–2 | Pick the right files cheaply |
| 2 | `context` tier 3 | Load implementation chunks |
| 3 | `context` + `compression: prune` | Shrink tier-3 payload; get `content_hash` |
| 4 | `retrieve` + `content_hash` | Restore full pre-prune payload when needed |

Use **`aggressive`** when budgets are very tight (fewer chunks/lines kept).

---

## MCP usage

**Compress on retrieval:**

```json
{
  "command": "context",
  "files": ["src/handlers/auth.py"],
  "query": "rate limit login endpoint",
  "tier": 3,
  "compression": "prune"
}
```

Response adds (when pruning actually saves tokens):

| Field | Meaning |
|-------|---------|
| `content_hash` | SHA-256 of cached pre-prune payload |
| `tokens_before_compress` | Tokens before prune |
| `tokens_used` | Tokens after prune |
| `compression_savings_ratio` | Fraction saved |
| `compression_method` | `prune_v1` |
| `retrieve_command` | `retrieve` |

**Restore verbatim:**

```json
{
  "command": "retrieve",
  "content_hash": "<hash from context response>"
}
```

Cache path: `.pareto-context-graph/payload_cache/<hash>.json` (local to the repo).

---

## Compression modes

| `compression` | Effect |
|---------------|--------|
| `none` | Default — no post-pack prune |
| `lossy` | Tier 2 only — drop private signatures |
| `prune` | Query-scored line/chunk trim + cache |
| `aggressive` | Stronger prune (fewer chunks/lines) |

Prune is **skipped** when it would not reduce token count (e.g. tiny files).

---

## Eval column

`eval --compress-stack` (alias `--headroom-stack`) runs tier 3 with `compression: prune` and reports
`graph_tokens_tier3` → `compressed_tokens` (`summary.compress_stack`).

## Eval gate (Phase C)

With `--compress-stack`, CI runs two regression checks:

1. **Retrieval** (`baseline.json`) — `mean_recall_at_5` / MRR / NDCG unchanged
2. **Compression** (`baseline-compress.json`) — `mean_recall_at_5` stable and `mean_compressed_tokens` does not rise >5% vs baseline

```bash
make eval-compress-check REPOS='fastapi=bench/fastapi httpx=bench/httpx'
# Refresh compression floors after intentional prune changes:
make eval-compress-baseline REPOS='fastapi=bench/fastapi httpx=bench/httpx'
```

Sanity gate (always with `--compress-stack`): `mean_compressed_tokens < mean_graph_tokens`,
`mean_stack_reduction_vs_graph >= 1.05`, and ≥35% of cases save tokens.

## Learned prune (Phase D)

`pareto-context-graph learn` also writes `.pareto-context-graph/prune_weights.json` — per-file **keep
bias** in `[-1, 1]` from feedback (`accept` / `mark_used` vs `reject` / unused).

When `prune_weights.json` exists, `compression: prune` applies learned biases:

| Bias | Effect on tier-3 prune |
|------|------------------------|
| **Positive** | Keep more query lines/chunks for files agents actually use |
| **Negative** | Aggressive prune on files often rejected |

Response fields: `learned_prune: true`, `learned_prune_paths` (count).

Disable with `PCG_FEATURE_LEARNED_PRUNE=0`. Force enable before weights exist with `=1`.

## Learned tier-1 prune

When `prune_weights.json` exists, tier-1 `context` can drop ranked rows with strongly
negative feedback bias (default: bias `< -0.3`) after pack, before optional summary prune.

| Bias | Effect on tier-1 rows |
|------|------------------------|
| **Positive** | Always kept (even in tail) |
| **Negative** | Dropped from tail (top 10 + seeds protected) |

Auto-enabled when weights exist. Response field: `learned_tier1_prune.dropped_count`.

Disable with `PCG_FEATURE_LEARNED_TIER1_PRUNE=0`. Force per request:
`"learned_tier1_prune": true` in the MCP `context` call.

CI gate: `check_learned_tier1_prune_gate` on fastapi concept cases (`make eval-check`).

```bash
pareto-context-graph learn   # writes prune_weights.json
pareto-context-graph eval --repo-map fastapi=bench/fastapi --compress-stack
```
