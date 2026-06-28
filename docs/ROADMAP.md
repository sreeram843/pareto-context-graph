# Roadmap — open work

Only **open** items live here. Shipped work is recorded in
[CHANGELOG.md](../CHANGELOG.md); how the system works is in
[ARCHITECTURE.md](ARCHITECTURE.md).

The north star is unchanged: **return the right files first, under an honest token
budget.** Gate every change on `make eval-check` (recall@5 / budget honesty).

## Now

- **End-to-end agent A/B numbers.** The harness is built and unit-tested
  (`scripts/agent_bench.sh`, `agent_transcript.py`), but the live arms need an
  authenticated `claude` CLI. Run `make agent-bench` and record the tool-call / token /
  cost reductions in [BENCHMARKS.md](BENCHMARKS.md). Use the memory-probe gate
  (`make memory-probe`) to pick repos the model can't already trace from memory — don't
  reuse famous repos for accuracy.
- **Validate cold-build targets on real T2/T3.** The 4.6× write speedup is measured via
  `scripts/build_microbench.py`; confirm the end-to-end goals (linux cold < 2 h, k8s
  full < 15 min) on actual clones, and that signed CI snapshots publish on schedule.

## Next

- **Evaluate a real embeddings backend.** `embed.py` ships a selector; the default
  `noop` backend is non-semantic. Run `scripts/embed_ab.py` with
  `PCG_EMBED_BACKEND=ollama` (or `local` via the `[embeddings]` extra) on the
  concept_*/pr_* subset (currently recall@5 0.7167 vs 0.8015 overall). Enable a default
  only if the lift justifies the dependency.
- **Learned priors when feedback grows.** Exposing the file-class/intent multiplier as a
  ranker feature overfit at current feedback volume (see BENCHMARKS "ceiling
  experiments"). Revisit once there is enough feedback to fit it without hurting holdout
  MRR.

## Known issues

- **SQLite `database is locked` under some test runners.** Automatic co-change decay
  opens a second writer connection on `context` requests; under heavy/parallel test runs
  (and some sandboxed filesystems) this can surface as a transient lock in
  `test_query_first` / `test_feedback_replay`. Decay already skips when the DB is busy —
  consider routing read-path decay through the existing connection or deferring it
  entirely to `sync`.
