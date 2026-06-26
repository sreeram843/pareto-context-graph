# Benchmarks

Run benchmark scripts after each major optimization:

```bash
PYTHONPATH=. python tests/perf/bench_build.py
PYTHONPATH=. python tests/perf/bench_query.py
```

Or use the Phase 6 harness:

```bash
# After building a graph:
pareto-context-graph bench --key fastapi --merge-results tests/eval/bench_results.json

# Full Tier 2/3 run (kubernetes or linux clone required):
make bench-huge REPOS=kubernetes=$(pwd)/bench/kubernetes
SKIP_BUILD=1 SKIP_INCREMENTAL=1 make bench-huge REPOS=kubernetes=$(pwd)/bench/kubernetes  # latency only
make bench-linux          # T3: clone + build + bench torvalds/linux (hours)
SKIP_BUILD=1 SKIP_INCREMENTAL=1 make bench-huge REPOS=linux=$(pwd)/bench/linux  # linux latency only
```

Track:
- build wall time
- **build phase profile** (`make profile-build` or `meta.build_profile` in graph.db)
- incremental update latency
- `context` p50/p95 (tier 1, hub-seeded)
- `graph.db` size
- token usage at tier 1/2/3

Record results here per profile (`tiny`, `medium`, `large`, `huge`).

### Typical operation times

| Operation | Typical time |
|-----------|--------------|
| Full build (5K commits) | ~30 s |
| Incremental update | &lt;2 s |
| `context` (typical repo) | &lt;1 s |
| `context` hub seed (k8s/linux) | **~6 ms** p95 hub-only |
| `search` FTS5 | &lt;50 ms |

### Build profiles

Auto-tuned from commit count when `--profile` is omitted.

| Profile | Commits | Since | Shards | Expansion | Iterations | Half-life | MMR λ |
|---------|---------|-------|--------|-----------|------------|-----------|-------|
| **tiny** | 5K | — | 1 | BFS | 1 | 365d | 0.7 |
| **medium** | 20K | 24mo | 2 | BFS | 1 | 270d | 0.7 |
| **large** | 50K | 18mo | 4 | BFS | 1 | 220d | 0.65 |
| **huge** | 80K | 12mo | 8 | RWR | 2 | 180d | 0.6 |

Auto-detection: &gt;100K commits → `huge`; &gt;50K → `large`; &gt;10K → `medium`; else `tiny`.

Clone recipes and CI tiers: [BENCHMARK_REPOS.md](BENCHMARK_REPOS.md)

## Latest Results

Environment:
- Python: `3.11+`
- Host: local dev (darwin)
- Command prefix: `PYTHONPATH=.` for `tests/perf/*`

### Synthetic build (`tests/perf/bench_build.py`)

| Profile | Commits | Files | Wall time |
|---------|---------|-------|-----------|
| tiny | 50 | 20 | 0.027s |
| medium | 500 | 120 | 0.146s |
| large | 2,000 | 200 | 0.283s |

### Synthetic query (`tests/perf/bench_query.py`)

300 commits / 80 files, `profile=large`, 15 samples:

| Metric | Value |
|--------|-------|
| p50 | 0.087s |
| p95 | 0.122s |
| max | 0.146s |

### Synthetic huge-profile stress (`tests/test_bench_stress.py`)

400 commits / 80 files, `profile=huge`, 2 context rounds:

| Metric | Value |
|--------|-------|
| context p95 | < 10s (CI gate) |
| incremental update | < 30s (noop path) |

### OSS benchmark repos (Phase 0 / 6)

Setup: `make bench-setup` · Results: `tests/eval/bench_results.json`

| Repo | Tier | Profile | Build | Files | Edges | graph.db | context p95 |
|------|------|---------|-------|-------|-------|----------|-------------|
| fastapi | T1 | tiny | 2s | 3,568 | 269,659 | 24 MB | — |
| httpx | T1 | tiny | 1s | 315 | 10,562 | 1.1 MB | — |
| kubernetes | T2 | huge | **792s** | 10,494 | 504,385 | **289 MB** | **0.006s** hub-only / 0.050s w/ query¹ |
| linux | T3 | huge | **37,877s** | 94,752 | — | **1.2 GB** | **0.132s** hub-only / 0.178s w/ query² |

¹ **kubernetes (2026-06-25 re-bench, post Phase 7.2):** SHA `e62c2b04709`, **5,150**
non-merge commits (12mo window; 50k cap; 4 shards). Build **792s** (~13 min).
Hub `go.mod` (degree 2,262). Hub-only `context` p95 **0.006s**; with-query p95
**0.050s** (9 samples, `timeout_ms=5000`, `truncated_samples=0`). `graph.db`
**289 MB**. Prior pre-7.2 latency: hub-only **2.41s**, with-query **6.31s**.

² **linux latency (2026-06-25 re-bench, `SKIP_BUILD=1`):** SHA `840ef6c78e6a`, existing
**100k-commit** graph (**1.2 GB** `graph.db`). Hub `MAINTAINERS`. Hub-only p95
**0.132s**; with-query p95 **0.178s** (9 samples, `timeout_ms=5000`,
`truncated_samples=0`). Cold build still **37,877s** (~10.5 h) from 2026-06-24
(Phase 10.7 full re-build pending). Prior post-7.2 low-latency run: hub-only **6 ms**;
with-query **62 ms** on same graph — variance is host/load sensitive; both well under
the **5 s** T3 target.

Tier 2/3 (kubernetes, linux): run `make bench-setup TIER=2` then
`make bench-huge REPOS=kubernetes=$(pwd)/bench/kubernetes`. The script records
build time, `graph.db` size, `stats`, `doctor`, incremental-update latency,
and hub-seeded `context` p50/p95 into `tests/eval/bench_results.json`.

### Phase 6 targets

| Repo | Metric | Target | Measured (2026-06-24) |
|------|--------|--------|------------------------|
| kubernetes | Build (50k cap, 12mo non-merge) | < 30 min | **792s** (~13 min) ✓ |
| kubernetes | Incremental update | < 5 s | not re-run in latest bench |
| kubernetes | `context` p95 tier 1 | < 2 s | hub-only **0.006s** ✓; with-query **0.050s** ✓ |
| linux | Build (100k commits, 24mo, 8 shards) | completes without OOM | **37,877s** (~10.5 h), **1.2 GB** db ✓ |
| linux | Hub-seed `context` | < 5 s or `truncated: true` | hub-only **0.132s** p95; with-query **0.178s** (2026-06-25 latency re-bench); **0** truncated ✓ |

### OSS hub context latency (post Phase 7.2)

Both measured with `SKIP_BUILD=1`, hub seed, `timeout_ms=5000`, 9 samples per mode.

**kubernetes (T2)** — hub `go.mod` (degree 2,262) · SHA `e62c2b04709`

| Mode | p50 | p95 | max |
|------|-----|-----|-----|
| Hub-only | 5 ms | 6 ms | 6 ms |
| With query | 5 ms | 50 ms | 80 ms |

**linux (T3)** — hub `MAINTAINERS` · SHA `840ef6c78e6a` · latency re-bench **2026-06-25**

| Mode | p50 | p95 | max |
|------|-----|-----|-----|
| Hub-only | 117 ms | 132 ms | 134 ms |
| With query | 115 ms | 178 ms | 213 ms |

Prior to Phase 7.2: kubernetes hub-only **2.41s** / with-query **6.31s**; linux hub-only **280s** / with-query **300s**.

### Build phase profile (Phase 10.1)

Builds now persist a `build_profile` meta blob (see `scripts/profile_build.py`,
`make profile-build REPO=… SHOW=1`). Phases:

| Phase | What it measures |
|-------|------------------|
| `git_log` | Streaming `git log -z --name-only` parse |
| `pair_aggregate` | In-memory pair merge across shards (sharded builds only) |
| `sqlite_writes` | `record_co_change` upserts into `co_changes` |
| `top_neighbours` | Per-file top-50 neighbour materialization |
| `search_indexes` | Symbol, content, and structural index rebuild |
| `files_fts` | FTS5 mirror rebuild |
| `commit` | Final WAL checkpoint |

#### Measured: fastapi (tiny profile, 5k commits, 2026-06-25)

| Phase | Seconds | % |
|-------|---------|---|
| git_log | 0.1 | 0.7 |
| sqlite_writes | 2.0 | 11.8 |
| top_neighbours | 0.4 | 2.3 |
| search_indexes | 14.3 | **85.1** |
| files_fts | 0.0 | 0.1 |
| **Total** | **16.8** | |

3,568 files · 269,659 edges · `search_indexes` dominates on T1 repos.

#### Estimated: kubernetes (huge, 5,150 commits, ~792 s wall)

| Phase | Est. % | Est. seconds | Notes |
|-------|--------|--------------|-------|
| git_log | ~1 | ~8 | Single streaming log pass |
| pair_aggregate | <1 | ~3 | 4-shard parallel in-memory merge |
| sqlite_writes | ~10 | ~80 | ~504k edge upserts (row-at-a-time) |
| top_neighbours | ~25 | ~200 | 10k files × neighbour ranking queries |
| search_indexes | ~55 | ~435 | ~20k indexable source files |
| files_fts + commit | ~8 | ~65 | FTS rebuild + checkpoint |
| **Total** | | **~792** | |

Run `make profile-build REPO=bench/kubernetes REPLAY=1` on a built graph to
refresh index-phase numbers without a full rebuild.

#### Estimated: linux (huge, 100k commits, ~37,877 s wall)

| Phase | Est. % | Est. seconds | Root cause / Phase 10.2 lever |
|-------|--------|--------------|-------------------------------|
| git_log | <1 | ~200 | 100k-commit `-z` log stream |
| pair_aggregate | <1 | ~50 | 8-shard merge |
| sqlite_writes | ~12 | ~4,500 | **684k** edge upserts; batch `executemany` (10.2) |
| top_neighbours | ~32 | ~12,100 | **40k** files × per-file `ORDER BY weight` scan (10.2) |
| search_indexes | ~48 | ~18,200 | **77k** indexable files; symbol + content SQLite writes (10.2) |
| files_fts + commit | ~7 | ~2,800 | FTS rebuild on large `files` table |
| **Total** | | **~37,877** | Target **< 4 h** via 10.2–10.3 |

**Conclusion:** git parse and pair extraction are not the bottleneck at T2/T3 scale.
**SQLite write amplification** (`record_co_change`, `top_neighbours`, `search_indexes`)
accounts for **> 90%** of linux build time.

**Phase 10.2 shipped:** `record_co_changes_bulk()` (10k-edge flushes), single-pass
`rebuild_top_neighbours` via SQL window functions, batched meta commits during finalize.

**Phase 10.3 shipped:** `_maybe_skip_build()` noop when `build_window_key` + `last_commit_hash`
match HEAD; `.pareto-context-graph/commit_window_cache.json` for git-log reuse; `update_search_indexes()`
skips files whose mtime/size match `index_state`; `incremental_update()` re-indexes only
touched paths. Repeat `build` on an unchanged repo is near-instant; incremental commits avoid
full `search_indexes` rebuild.

**Still open:** linux 2× cold-build target needs **10.7 re-bench** (optional FTS deferral).

**Phase 10.4 shipped:** `pareto-context-graph doctor` prints a cold-rebuild estimate (wall time
range + `graph.db` size) from git commit/file counts and profile presets, scaled against
fastapi / kubernetes / linux measured anchors. Override with `--profile`, `--commits`,
`--since`, `--shards` (same as `build`).

```bash
make profile-build REPO=bench/linux SHOW=1       # after rebuild
make profile-build REPO=bench/linux REPLAY=1     # index phases only
PYTHONPATH=. python scripts/profile_build.py --repo bench/fastapi --show
```

Weekly CI runs synthetic stress via `.github/workflows/bench-weekly.yml`.
OSS Tier 2/3 numbers are recorded manually after `bench-huge` on a machine with
the clone.
