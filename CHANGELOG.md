# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added (Phase 4 — ceiling experiments)

- **Embeddings backend selector** (`PCG_EMBED_BACKEND` ∈ noop/openai/ollama/local,
  Phase 4.2): records the backend in the embedding index and reuses it at query time —
  fixing a latent bug where queries were always encoded with the hash backend (vector-
  space mismatch). Real local model is opt-in via the `[embeddings]` extra (fastembed),
  never bundled — the default stays stdlib-only. A/B harness `scripts/embed_ab.py`
  targets the low-recall concept_*/pr_* subset (recall@5 0.7167 vs 0.8015 overall).
- **`python -m pareto_context_graph`** now works (added `__main__.py`).
- **RRF rank-fusion mode** (`PCG_RANK_FUSION=rrf`, Phase 4.3): reciprocal-rank fusion
  of the rank-phase signals as an A/B alternative to the tuned additive weighted-sum.
  Ties recall@5 (0.8015) with no per-signal weights but trails on ordering (MRR −0.005,
  NDCG −0.009), so weighted-sum stays default. `rrf_rank_relevance` unit-tested.
- **Documented negative result (4.1):** exposing the file-class/intent multiplier as a
  learned-ranker feature overfit sparse feedback (holdout MRR 0.635→0.563) and was
  reverted; the feedback-replay holdout test now asserts the holdout-gated-save
  invariant instead of assuming a ranker gain always exists. See `docs/BENCHMARKS.md`.

### Added (Phase 3 — agent steering)

- **Tier-2 signature collapse** (`collapse_signatures`, Phase 3.3): tier-2 responses
  now drop exact-duplicate signatures and collapse overload clusters (>N declarations
  of one name) into the first few + a `# … +N more <name>(...) overloads` marker,
  saving budget on overload-heavy files. Default keeps 2/name (`PCG_TIER2_MAX_PER_NAME`,
  0 disables). Tests in `tests/test_context_ranking.py`. (3.1 MCP initialize playbook,
  3.2 native `watchfiles` watcher + staleness banners, and the `explore` preset /
  `PCG_MCP_COMMANDS` schema trim were already implemented and are covered by
  `test_week3_mcp.py`.)

### Added (Phase 2 — cold-build reliability validation)

- **Reproducible cold-build microbenchmark** (`scripts/build_microbench.py`) isolating
  SQLite write amplification. Confirms the fast-load path (`enter_cold_bulk_load`,
  Phase 2.2) + pair pre-aggregation (2.3) + Python top-neighbour rebuild (2.1) deliver
  a **4.6× insert speedup at 1M edges** (9.77s → 2.13s; 3.5× at 200k) with identical
  edge counts. Numbers + reproduction in `docs/BENCHMARKS.md`. The build path itself
  (cold bulk load, aggregation, default exclusions, lazy/resumable search index,
  huge/huge-full caps, signed CI snapshots) was already implemented and is covered by
  `test_week1_build.py` / `test_week2_build.py`.

### Added (Phase 1 — real agent A/B + LLM judge + flow ground truth)

- **Flow ground truth** (`tests/eval/flows/ground-truth.json`): verified `file:line`
  call paths + must-hit symbols + dynamic-boundary notes per repo, for end-to-end
  flow questions. Loader/validator in `flows.py`; `tests/test_flows.py` re-verifies the
  cited symbols still resolve in the cloned repo (anti-rot).
- **Stream-json transcript parser** (`agent_transcript.py`): parses
  `claude -p --output-format stream-json` into real tool-call / token / cost / turn
  metrics; `aggregate_runs` (median of N≥4), `arm_comparison` (pcg-vs-baseline
  reductions), `check_agent_ab_gate`. Fully unit-tested (`tests/test_agent_transcript.py`).
- **Live agent A/B harness**: `scripts/agent_bench.sh` runs each flow with the pcg MCP
  server vs an empty `--strict-mcp-config` baseline; `scripts/memory_probe.sh` gates
  out repos the model can trace unaided; `scripts/eval_judge.mjs` is a no-tools,
  neutral-cwd LLM judge. Make targets `agent-bench`, `agent-bench-gate`,
  `memory-probe`; CI runs the gate (no-op until a transcript run is committed).
  Requires an authenticated `claude` CLI. Docs: `docs/BENCHMARKS.md`.

### Fixed (token / packing regression)

- **Tier-1 map flooded the budget.** Non-high-fanout tier-1 packing had no file
  cap, so a generous budget pulled in the long low-relevance `import`/`directory`
  tail (~20+ files/case). Added `TIER1_MAX_FILES` (default 12, `PCG_TIER1_MAX_FILES`);
  dropped candidates remain in `dropped_paths` and via tier escalation. Mean tier-1
  tokens **2599 → 1309** on the T1 eval with **recall@5 / MRR / candidate_pool_recall
  unchanged** and budget_honesty 0.98 → 1.00. Regression test:
  `test_tier1_map_is_capped`.

### Fixed (ranking regression recovery — Phase 0)

- **Hub-penalty inversion.** The `log2(2 + degree)` hub penalty divided every
  moderately-connected file, and over fastapi's narrow co-change weight range
  (~1.0–1.44) it ranked junk degree-0 directory siblings above genuine degree-30
  co-change partners. Added a degree floor (`HUB_DEGREE_FLOOR=80`,
  `hub_penalty_factor`) so only true hubs are suppressed, plus a small low-degree
  tie-breaker (`HUB_TIEBREAK_EPS=0.1`, `hub_tiebreak`) so specificity orders
  otherwise-tied siblings. A/B via `PCG_ABLATE_HUBFLOOR=1`.
- **Community boost over-domination.** `COMMUNITY_RANK_BOOST` lowered 12.0 → 3.0
  (env-tunable `PCG_COMMUNITY_RANK_BOOST`) so cluster membership is worth ~one
  strong co-change edge instead of burying genuine partners in other clusters.
- **Deterministic eval gate.** `run_evaluation` now suppresses automatic co-change
  edge decay for its duration — a regression gate must measure ranking, not graph
  age. Eval is now bit-reproducible across runs.
- **Ranker feature.** `entry_diagnostics` keeps the raw `log2(2+degree)` as the
  learned-ranker feature (the floor applies only to hand-scoring), preserving the
  degree signal the LambdaMART holdout relies on.

### Changed (eval ground truth — re-baselined)

- **Golden cases cleaned of release/packaging/CI files.** A code-context tool
  should not be graded on predicting version-bump companions, so
  `pyproject.toml`, `setup.py`, `CHANGELOG.md`, `README.md`, `.github/workflows/*`,
  and `release-notes.md` were removed from `expected_top_files`. 6 pure
  release-automation cases (gold was entirely such files) were dropped; 8 mixed
  cases keep their code expectations. `tests/eval/baseline.json` re-recorded on the
  cleaned set (74 → 68 T1 cases). **Reason for re-baseline:** the prior baseline
  rewarded surfacing release files; on the cleaned, code-focused set recall@5 is
  **0.80** (MRR 0.91, NDCG 0.78), up from the pre-fix 0.70, and the gate is green.

#### PRF
- **Pseudo-relevance co-change expansion** (query-only path): top lexical hits seed
  a co-change walk to pull in coupled files text search misses. Non-evicting
  (`_cap_prf_relevance`), capped at 8, gated off concept queries, A/B via
  `PCG_ABLATE_PRF=1` / `PCG_FEATURE_PRF_COCHANGE=0`.

### Added

#### Onboarding & CLI
- `pareto-context-graph init` — cold build (or `--from-snapshot`), optional MCP `install`, printed next steps.
- `pareto-context-graph sync` — incremental graph update; `--with-index` catches up deferred search indexes.
- `pareto-context-graph index` — build or resume lazy symbol/content indexes.
- `pareto-context-graph affected` — suggest tests for changed files (reverse structural walk); MCP `affected` command.
- `pareto-context-graph install` / `uninstall` v2 — auto-configure Cursor, Copilot, Claude, Windsurf MCP + steering markers (`agent_install.py`).
- `pareto-context-graph detect-changes` and MCP `detect_changes` — git diff blast radius + index staleness.
- `pareto-context-graph architecture-report` — write `ARCHITECTURE_REPORT.md` from graph stats.

#### Build pipeline (Weeks 1–2)
- In-memory `top_neighbours` via `neighbour_cache.py` (avoids SQL window sorts on large graphs).
- Cold-build fast-load SQLite profile (`enter_cold_bulk_load` / `exit_cold_bulk_load`; `PCG_COLD_BUILD_FAST`).
- Default path exclusions + `.pareto-context-graph/config.json` (`repo_config.py`).
- Phased build: co-change first, lazy search index on `huge` profiles (`search_index_status`: `pending` | `complete`).
- Batched search-index commits with resume (`PCG_INDEX_COMMIT_BATCH`; `index_state` mtime/size skip).
- `shards=1` pre-aggregated co-change merge path.

#### MCP agent UX (Weeks 3–4)
- Rich MCP `initialize` instructions and agent playbook (`server_instructions.py`).
- Native file watcher with debounce (`daemon.py` rewrite); poll fallback when OS watcher unavailable.
- Staleness banners on `context` / `explore` / `search` / `retrieve`; catch-up on connect (`staleness.py`).
- MCP `explore` preset (query-only `context`); trim exposed commands via `PCG_MCP_COMMANDS`.
- Signed kubernetes snapshot CI (`.github/workflows/bench-t2.yml`); linux monthly bench (`.github/workflows/bench-t3-linux.yml`).

#### Retrieval & signals (Weeks 5–6, Phase 11)
- Headless agent A/B harness: `eval --agent-ab`, `agent_ab.py`, `make eval-agent-ab-baseline` / `eval-agent-ab-check`.
- Cross-file coverage in `doctor`, `stats`, and `graph_stats()` (`connected_files`, `cross_file_coverage_pct`).
- Tree-sitter symbol index **on by default** when `[treesitter]` is installed (`PCG_FEATURE_TREESITTER=0` to opt out).
- FastAPI/Flask route edges (`route_edges.py`; `kind: "route"` in structural blast).
- Multi-repo MCP routing: `serve --repo-map KEY=PATH` and optional `repo_key` on tool calls (`repo_registry.py`).
- Watcher error metrics — no silent swallow (`watcher_health.py`; `cgmcp_watcher_errors_total`).

#### Phase 15 — Codified context bridge
- `knowledge_gap`, `routing_hints`, `spec_drift`, `spec_context` on `context` responses.
- `list_subsystems` / `subsystem_files` MCP commands (manual + auto directory map).
- `response_version: 3` with structured `code_context` and `spec_context`.
- `codify_suggestion` in feedback hooks when paths are rejected repeatedly.
- See PHASES_CODIFIED_CONTEXT.md.

#### Eval, ranking & compression
- `eval --agent-ab` scorecard: median tool calls, file reads, tokens, wall time, recall@5 vs grep+read baseline.
- `eval --ablation` and `PCG_ABLATE_*` per-signal ablation study.
- `eval --feedback-replay` held-out MRR gate; grep counterfactual gate on `make eval-check`.
- `eval --compress-stack` + `baseline-compress.json` compression regression gate.
- Confidence calibration report (`confidence_calibration` in eval summary).
- Tier-1 `pick_reason` on default `context` responses (full `diagnostics` still opt-in).
- `retrieval_confidence` field and fallback telemetry on `context` responses.
- Phase 11.4 summary prune and 11.6 learned tier-1 prune gates.
- TypeScript/JavaScript tree-sitter symbols (`tree-sitter-typescript` optional extra).

#### Infrastructure
- Context pipeline refactor: `context_pipeline.execute_context_pipeline()`, `context_pipeline_phases.py`, `context_ranking.py`.
- `taxonomy.py` — centralized query intent, file class, and noise-path rules.
- Versioned TTL caches (`repo_caches.py`); bulk co-change edge decay on context path.
- Holdout-gated ranker save in `feedback_replay.py`.
- PyPI publish workflow (`.github/workflows/publish.yml`).
- CI quality gates: ruff, mypy, pytest coverage (`.github/workflows/quality.yml`).
- Example `pcg-affected` workflow (`.github/workflows/pcg-affected.yml.example`).

#### Tests
- Product plan suites: `tests/test_week1_build.py` … `tests/test_week6_quality.py`, `tests/test_week5_product.py`.
- Golden eval expanded (fastapi 50 cases, kubernetes 24, httpx); `baseline-agent-ab.json` placeholder.

### Changed
- **Default onboarding path:** `init` + `sync` documented in [QUICKSTART.md](docs/QUICKSTART.md) and [COMMANDS.md](docs/COMMANDS.md); T2/T3 snapshot flow uses `init --from-snapshot` or `sync --with-index` after `git pull`.
- `huge` / `huge-full` profiles: deferred search index by default; `--with-search-index` or `pareto-context-graph index` for eager build.
- `install` writes `.cursor/mcp.json`, `AGENTS.md` / Copilot steering markers, optional `--watch` on `serve` args.
- `doctor` reports build estimate, symbol index mode, staleness, watcher health, and cross-file coverage.
- Eval learning isolation (`clear_learning_state` per repo) for reproducible regression runs.
- `Makefile`: T1 default for `make eval-check`; `eval-agent-ab-baseline` / `eval-agent-ab-check` targets.
- PHASES.md merged with former LEFTOVERS.md — single maintainer roadmap (milestones, open items, phase status tables).

### Removed
- `docs/TOKEN_REDUCTION_AGENT_RESEARCH.md` — research notes (content superseded by shipped features + [CONTEXT_COMPRESSION.md](docs/CONTEXT_COMPRESSION.md)).
- `docs/PRODUCTION_ROADMAP.md` — early handoff spec (superseded by PHASES.md).
- `docs/CRG_INSPIRATIONS.md` — design archaeology (CRG attribution retained in README).
- `docs/LEFTOVERS.md` — merged into PHASES.md.

### Fixed
- `signing.py` no longer swallows all exceptions during Ed25519 verification.
- CI synthetic git repos hardened (`build_repo.py` fsync + git config); phase7 timing threshold relaxed for runners.
- SQLite edge decay uses bulk SQL + `busy_timeout`; context requests skip decay when the DB is locked.
- Co-change decay runs on a standalone writer connection (avoids pool read/write lock contention on large graphs).

## [0.1.0] - 2026-06-01

### Added
- Initial MCP server: git co-change graph, query-first context, eval harness, feedback loop.
