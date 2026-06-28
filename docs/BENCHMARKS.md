# Benchmarks

How to measure the two things that matter: **retrieval quality** (does the right file
rank high, under budget) and **build/serve performance**. All numbers below are
reproducible from the commands shown; treat them as current measurements, not targets.

## Running

```bash
# Retrieval quality regression gate (Tier 1: fastapi + httpx)
make eval-check REPOS="fastapi=$(pwd)/bench/fastapi httpx=$(pwd)/bench/httpx"

# Per-signal ablation table (which signals carry recall@5)
make eval-ablation REPOS="fastapi=$(pwd)/bench/fastapi httpx=$(pwd)/bench/httpx"

# Cold-build write-path microbenchmark (no clone needed)
python3 scripts/build_microbench.py --edges 1000000 --files 1500

# Latency/throughput on a built graph
pareto-context-graph bench --key fastapi --merge-results tests/eval/bench_results.json
```

## Benchmark repos

| Tier | Repos | First-time setup |
|------|-------|------------------|
| **T1** | fastapi, httpx | `pareto-context-graph build --profile tiny --commits 5000` |
| **T2** | kubernetes | [CI snapshot](CI_SNAPSHOTS.md) → `build --from-snapshot` (cold build ~13 min) |
| **T3** | linux | [team snapshot](CI_SNAPSHOTS.md) (cold build ~10 h) |

| Repo | graph.db | Cold build | Hub `context` p95 |
|------|---------:|-----------:|------------------:|
| fastapi | ~24 MB | ~2 s | — |
| httpx | ~1 MB | ~1 s | — |
| kubernetes | ~289 MB | ~13 min (or snapshot < 5 min) | ~6 ms hub-only |
| linux | ~1.2 GB | ~10 h (or team snapshot) | ~0.13 s hub-only |

Build profiles are auto-selected from commit count (>100K → `huge`, >50K → `large`,
>10K → `medium`, else `tiny`); override with `--profile`. See `profiles.py` for the
exact parameters (commit window, shards, expansion, decay half-life, MMR λ).

## Retrieval quality (eval)

Tier-1 golden set, fastapi + httpx (74 cases). Gate: `make eval-check`.

| Metric | Value |
|--------|------:|
| recall@5 | **0.8015** |
| MRR | 0.9130 |
| NDCG@10 | 0.7802 |
| mean tokens (tier-1 map) | ~1310 |
| budget honesty | 1.00 |

The eval also reports **candidate-pool recall** (was the gold file retrieved at all)
separately from final recall@5 (did it survive ranking), and a per-signal ablation
table. The dominant signal is the git co-change graph (ablating it costs ~0.18 recall);
lexical signals and the floored hub penalty contribute the rest.

## Cold-build performance

The dominant cost of a large cold build is SQLite write amplification, not git or
parsing. The fast-load path (`enter_cold_bulk_load`: `journal_mode=OFF`,
`synchronous=0`, co-change indexes dropped during the bulk insert and recreated after)
plus Python-side pair pre-aggregation and the in-memory top-neighbour rebuild target
exactly that cost. Toggle with `PCG_COLD_BUILD_FAST=0` (default on).

`python3 scripts/build_microbench.py`:

| edges | legacy insert | fast insert | speedup |
|------:|--------------:|------------:|--------:|
| 200k | 1.23 s | 0.35 s | 3.5× |
| 1M | 9.77 s | 2.13 s | 4.6× |

Edge counts are identical across modes; the speedup grows with edge count, which is
where it matters (linux/k8s have millions of edges). For T2/T3, prefer
`pareto-context-graph snapshot import` (signed CI snapshots) over a cold build —
see [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md).

## Agent A/B harness

A real end-to-end comparison: run a coding agent on a flow question twice — once with
the pcg MCP server, once with an empty MCP config (`--strict-mcp-config`, i.e. plain
Read/Grep/Bash) — and parse the `claude -p --output-format stream-json` transcripts for
tool-call / token / cost counts. Needs an authenticated `claude` CLI.

```bash
make memory-probe          # keep only repos the model can't trace unaided
make agent-bench N_RUNS=4  # run both arms → tests/eval/agent-ab.json
make agent-bench-gate      # fail if pcg loses to the baseline arm
```

- Flow ground truth: `tests/eval/flows/ground-truth.json` (verified `file:line` call
  paths; self-checked by `tests/test_flows.py`).
- Transcript parser + medians + gate: `pareto_context_graph.agent_transcript`
  (unit-tested).
- LLM judge: `scripts/eval_judge.mjs` (no tools, neutral cwd, JSON verdict).

Record the per-flow tool-call / token / cost reductions here once the live arms run.

## Ranking experiments

Run on the Tier-1 eval, documented win-or-lose.

- **RRF vs additive weighted-sum** (`PCG_RANK_FUSION=rrf`): RRF ties recall@5 (0.8015)
  with no per-signal weights but trails on ordering (MRR −0.005, NDCG −0.009). Kept
  weighted-sum as default; RRF confirms the ranking isn't brittle to the hand weights.
- **File-class/intent as a learned-ranker feature** (reverted): overfit sparse feedback
  and regressed holdout MRR (0.635 → 0.563). The prior stays a scoring multiplier until
  there is enough feedback to fit it. The holdout test now asserts the holdout-gated-save
  invariant rather than assuming a gain always exists.
- **Embeddings backend selector** (`PCG_EMBED_BACKEND`): the default `noop` backend is
  non-semantic (embed Δ≈0). `scripts/embed_ab.py` A/Bs a real backend on the
  concept_*/pr_* subset (20 cases at recall@5 0.7167 vs 0.8015 overall) — the cluster
  most likely to benefit. See [OPTIONAL_FEATURES.md](OPTIONAL_FEATURES.md) to enable one.
