# Evaluation golden set

Regression harness for retrieval quality and token efficiency. Used by `make eval`
and CI to catch ranking regressions before they ship.

## Layout

```
tests/eval/
├── README.md           # this file
├── pins.json           # pinned commit SHAs per benchmark repo
├── bench_results.json  # merged OSS bench metrics (build + context latency)
├── baseline.json       # T1 regression gate (fastapi + httpx)
├── baseline-kubernetes.json  # T2 regression gate (kubernetes)
└── golden/
    ├── fastapi/
    │   └── cases.json  # Tier 1 (CI smoke, 50 cases)
    ├── httpx/
    │   └── cases.json  # Tier 1 (CI smoke, 9 cases)
    └── kubernetes/
        └── cases.json  # Tier 2 (bench-t2 weekly, 20 cases)
```

## Case schema

Each entry in `golden/<repo_key>/cases.json`:

| Field | Required | Description |
|-------|----------|-------------|
| `case_id` | yes | Stable identifier, e.g. `fastapi_oauth2_password` |
| `repo_key` | yes | Must match directory name under `golden/` |
| `seed_files` | usually | Files the user is editing; empty for query-only cases (Phase 3+) |
| `query` | yes | Natural-language question or task |
| `expected_top_files` | yes | Ground-truth paths that should rank highly |
| `tier` | no | Default `1` (summaries only) |
| `token_budget` | no | Default `50000` |
| `max_depth` | no | Default `2` |
| `min_weight` | no | Default `2` |
| `category` | no | `concept`, `blast`, `co_change`, `hub` — for per-category metrics |
| `notes` | no | PR link, incident ref, or curator rationale |

### Example

```json
{
  "cases": [
    {
      "case_id": "fastapi_oauth2_password",
      "repo_key": "fastapi",
      "seed_files": ["fastapi/security/oauth2.py"],
      "query": "how does OAuth2 password flow work",
      "expected_top_files": [
        "fastapi/security/oauth2.py",
        "tests/test_security_oauth2.py"
      ],
      "tier": 1,
      "token_budget": 8000,
      "category": "concept",
      "notes": "Concept query with a narrow seed."
    }
  ]
}
```

## Sourcing ground truth

1. **Real PRs** — `scripts/expand_golden_from_prs.py` scans merge commits and squash-style `(#NNNN)` commits; `notes` include the GitHub PR URL.
2. **Co-change neighbours** — `scripts/expand_golden_cases.py` from graph topology.
3. **Blame / ownership** — for “how does X work”, files with most commits on symbol X.
4. **Import graph** — static followers of a seed module (sanity check, not sole source).
5. **Do not** use the same graph under test as the only ground-truth source (CRG circularity lesson).

Target **≥ 50 cases** for the primary Tier-1 repo; **≥ 10** to start gating CI.

## Running eval

```bash
# Build graph in the target repo first
cd /path/to/fastapi && pareto-context-graph build

# Run cases for that repo key
make eval REPOS=fastapi=/path/to/fastapi

# Or via module
python3 -m pareto_context_graph.eval fastapi=/path/to/fastapi

# Refresh baseline intentionally (after verified improvement)
make eval-baseline REPOS='fastapi=bench/fastapi httpx=bench/httpx'
make eval-baseline REPOS='kubernetes=bench/kubernetes' BASELINE=tests/eval/baseline-kubernetes.json

# Regression check (CI)
make eval-check REPOS='fastapi=bench/fastapi httpx=bench/httpx'
make eval-check-kubernetes   # T2 weekly in bench-t2.yml

# Zero-recall gate (fails if any case has recall@5 = 0)
make eval-audit REPOS='fastapi=bench/fastapi httpx=bench/httpx'
make eval-audit-kubernetes
```

## Metrics

| Metric | Meaning |
|--------|---------|
| `recall@5` | Share of `expected_top_files` found in top 5 results |
| `MRR` | Mean reciprocal rank of first expected hit |
| `nDCG@10` | Discounted cumulative gain at 10 |
| `tokens_used` | Tokens returned by `context` |
| `token_efficiency` | relevant hits / `tokens_used` |
| `budget_honesty` | 1.0 when `tokens_used ≤ token_budget` |
| `reduction_vs_corpus` | naive full-repo tokens / graph tokens |
| `reduction_vs_agent` | grep-top-3 baseline tokens / graph tokens |

## Regression policy

PRs that touch retrieval (`server.py`, `blast.py`, `chunks.py`, `walk.py`, `store.py`)
must paste eval summary in the PR description. CI fails if `recall@5`, MRR, or `nDCG@10`
drop by **> 2 absolute points** vs the repo baseline (`baseline.json` for T1;
`baseline-kubernetes.json` for T2) without maintainer override.

`scripts/audit_golden_cases.py` fails on any case with `recall@5 = 0` (run via `make eval-audit`).

See [docs/PHASES.md](../docs/PHASES.md) for the full execution plan.

## Benchmark results (`bench_results.json`)

Stress metrics from `make bench-huge` / `pareto-context-graph bench`:

| Field | Meaning |
|-------|---------|
| `build_seconds` | Wall time for full graph build |
| `graph_db_bytes` | SQLite database size |
| `context_latency.hub_seed` | Top-hub file used for latency samples |
| `context_latency.timeout_ms` | Deadline passed to `context` (default 5000) |
| `context_latency.truncated_samples` | Samples that returned `truncated: true` |
| `context_latency.hub_only_context` | p50/p95/max with hub seed only |
| `context_latency.context` | p50/p95/max with hub seed + query |

Latest OSS hub latency (post Phase 7.2): kubernetes `go.mod` hub-only p95 **0.006s**,
with-query **0.050s**; linux `MAINTAINERS` hub-only **0.006s**, with-query **0.062s**.
See [docs/BENCHMARKS.md](../../docs/BENCHMARKS.md).
