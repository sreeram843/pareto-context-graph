# Benchmark repositories

Open-source repos for eval quality gates and scale stress tests. See
[PHASES.md](PHASES.md) for how tiers fit the execution plan.

## Default onboarding (read this first)

| Tier | Repo | **Recommended first-time setup** | Avoid unless necessary |
|------|------|----------------------------------|-------------------------|
| **T1** | fastapi, httpx | `pareto-context-graph build --profile tiny --commits 5000` | — |
| **T2** | kubernetes | **[CI snapshot](CI_SNAPSHOTS.md#kubernetes-t2--recommended-flow)** → `build --from-snapshot` | Cold build ~13 min |
| **T3** | linux | **[Team snapshot](CI_SNAPSHOTS.md#linux-t3--team-snapshot)** or shared export | Cold build ~10.5 h |

Full snapshot guide: **[CI_SNAPSHOTS.md](CI_SNAPSHOTS.md)**

```bash
# T2 fast path (after cloning kubernetes)
pareto-context-graph build --from-snapshot /path/to/kubernetes-graph-snapshot.tar.gz
pareto-context-graph doctor
```

---

## Tiers

| Tier | Repos | CI workflow | Schedule |
|------|-------|-------------|----------|
| **T1** | fastapi, httpx | [eval.yml](../.github/workflows/eval.yml) | Every PR (path filter) |
| **T2** | kubernetes | [bench-t2.yml](../.github/workflows/bench-t2.yml) | Sunday 07:00 UTC + manual |
| **T1 synth** | synthetic | [bench-weekly.yml](../.github/workflows/bench-weekly.yml) | Sunday 06:00 UTC |

### CI details

**eval.yml (T1)** — on PRs touching retrieval/eval code:
- Clone + build fastapi (pinned SHA)
- `make eval-check`, `--feedback-replay`, hub-timeout tests (fastapi; kubernetes/linux when local graph present)
- Unit tests including `test_features.py`

**bench-t2.yml (T2)** — kubernetes:
- Clone with `--filter=blob:none`, pinned SHA from `pins.json`
- Build **5,000 commits** (12mo, 4 shards) — bounded for ~120 min GHA budget
- `make bench-huge` with `SKIP_INCREMENTAL=1`
- Eval gate (Phase 9.5): `make eval-check-kubernetes` + `make eval-audit-kubernetes` (**24** cases, `baseline-kubernetes.json`)
- **Signed snapshot** (Phase 10.5): `PCG_SNAPSHOT_KEY` → `kubernetes-graph-snapshot.tar.gz` + `.sig.json` artifact
- `pytest -k kubernetes` hub-timeout tests
- Gate: hub-only context p95 &lt; **1s** (post Phase 7.2; was 15s)
- Uploads `bench_results.json` as artifact

Trigger manually: **Actions → Bench T2 (Kubernetes) → Run workflow**

For full local T2: `make bench-setup TIER=2` (or `SKIP_CLONE=1` to rebuild only).

**Fast path (recommended):** download the weekly `kubernetes-graph-snapshot` artifact and run
`pareto-context-graph build --from-snapshot <path>` (see [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md)).

---

## Tier 1 — CI smoke

### fastapi/fastapi

- **~3,000** tracked files; **269k** co-change edges; CRG benchmark reference repo
- Pinned SHA: see [tests/eval/pins.json](../tests/eval/pins.json)
- Clone: `git clone --depth=5000 https://github.com/fastapi/fastapi.git`
- Build: `pareto-context-graph build --profile tiny --commits 5000`
- **Measured (2026-06-24):** build ~2s, `graph.db` ~24 MB
- Eval: `make eval REPOS=fastapi=$PWD`

### encode/httpx

- **~125** tracked files; fast sanity check
- Clone: `git clone --depth=5000 https://github.com/encode/httpx.git`
- Build: `pareto-context-graph build --profile tiny --commits 5000`
- **Measured (2026-06-24):** build ~1s, `graph.db` ~1.1 MB

---

## Tier 2 — Large monorepo (nightly)

### kubernetes/kubernetes

- **10k+** files; **~130k** commits; Go hub files, realistic enterprise scale
- **Onboarding:** [CI_SNAPSHOTS.md — kubernetes flow](CI_SNAPSHOTS.md#kubernetes-t2--recommended-flow) (weekly artifact)
- Clone: `git clone --filter=blob:none https://github.com/kubernetes/kubernetes.git`
- Cold build (if no snapshot):
  ```bash
  pareto-context-graph build --profile huge \
    --since "12 months ago" \
    --commits 50000 \
    --shards 4
  ```
- **Measured (2026-06-24):** build **792s** (~13 min), **5,150** non-merge commits
  (12mo window; 50k is a cap), `graph.db` **289 MB**, hub `go.mod` (degree 2,262).
  Hub-only context p95 **0.006s**; with-query **0.050s** (`timeout_ms=5000`,
  `truncated_samples=0`; post Phase 7.2 re-bench).
- Eval: `tests/eval/golden/kubernetes/cases.json` (**24** cases; `baseline-kubernetes.json`)
- Hub-timeout tests: `tests/test_hub_timeout.py` (kubernetes `go.mod`)

---

## Tier 3 — Million-commit stress (weekly)

### torvalds/linux

- **~1.4M** commits; **~95k** tracked files — git-log and SQLite stress
- **Onboarding:** [CI_SNAPSHOTS.md — linux team snapshot](CI_SNAPSHOTS.md#linux-t3--team-snapshot)
- **Do not** run unbounded full history in CI
- Clone: `git clone --filter=blob:none https://github.com/torvalds/linux.git`
- Cold build (last resort):
  ```bash
  pareto-context-graph build --profile huge \
    --since "24 months ago" \
    --commits 100000 \
    --shards 8
  ```
- **Measured (2026-06-24 build / 2026-06-25 latency):** cold build **37,877s** (~10.5 h),
  **100,000** commits (24mo, 8 shards), `graph.db` **1.2 GB**, hub `MAINTAINERS`.
  Latency re-bench: hub-only p95 **0.132s**; with-query **0.178s** (see [BENCHMARKS.md](BENCHMARKS.md)).
- Script: `make bench-linux` or `scripts/bench_huge.sh linux=...`
- **Share with team:** `pareto-context-graph snapshot export bench/backups/linux-graph-YYYYMMDD.tar.gz`
- Latency only (existing graph): `SKIP_BUILD=1 SKIP_INCREMENTAL=1 make bench-huge REPOS=linux=$(pwd)/bench/linux`
- Hub-timeout tests: `tests/test_hub_timeout.py` (linux `MAINTAINERS`)

Record results in [BENCHMARKS.md](BENCHMARKS.md): wall time, `graph.db` size,
`context` p95, `doctor` output.

---

## Disk and memory

| Repo | Clone (filter) | graph.db (measured) | Build time | Hub `context` p95 |
|------|----------------|---------------------|------------|-------------------|
| fastapi | depth 5000 | ~24 MB | ~2 s | — |
| httpx | depth 5000 | ~1.1 MB | ~1 s | — |
| kubernetes | ~500 MB (est.) | **289 MB** | **792s** (~13 min) or **snapshot < 5 min** | **0.006s** hub-only |
| linux | ~2 GB+ (est.) | **1.2 GB** | **37,877s** (~10.5 h) or **team snapshot** | **0.132s** hub-only (2026-06-25) |

Latest numbers: [tests/eval/bench_results.json](../tests/eval/bench_results.json).

Use `--filter=blob:none` for T2/T3 to avoid downloading full blob history.

## Quick start (Phase 0)

See [QUICKSTART.md](QUICKSTART.md) and:

```bash
pip install -e ".[dev]"
make bench-setup          # clone T1 repos + build graphs + pin SHAs
make bench-smoke          # stats + doctor on fastapi + httpx
```

Tier 2/3 (local):

```bash
# Recommended: snapshot first (see CI_SNAPSHOTS.md)
pareto-context-graph build --from-snapshot /path/to/kubernetes-graph-snapshot.tar.gz

# Or cold build when no snapshot:
make bench-setup TIER=2
make bench-huge REPOS=kubernetes=$(pwd)/bench/kubernetes
make bench-linux          # T3 cold build (hours)
```

---

## Baseline methodology

Compare three token baselines (see [code-review-graph REPRODUCING](https://github.com/tirth8205/code-review-graph/blob/main/docs/REPRODUCING.md)):

1. **Naive corpus** — all source file tokens (upper bound; not realistic)
2. **Agent grep** — top-3 files by keyword match count (realistic agent behavior)
3. **Graph query** — `pareto_context_graph context` tier 1 (this tool)

Report `reduction_vs_corpus` and `reduction_vs_agent` separately. Prefer
`reduction_vs_agent` for honest comparisons.
