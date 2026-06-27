# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
- See [PHASES_CODIFIED_CONTEXT.md](docs/PHASES_CODIFIED_CONTEXT.md).

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
- [PHASES.md](docs/PHASES.md) merged with former [LEFTOVERS.md](docs/LEFTOVERS.md) — single maintainer roadmap (milestones, open items, phase status tables).

### Removed
- `docs/TOKEN_REDUCTION_AGENT_RESEARCH.md` — research notes (content superseded by shipped features + [CONTEXT_COMPRESSION.md](docs/CONTEXT_COMPRESSION.md)).
- `docs/PRODUCTION_ROADMAP.md` — early handoff spec (superseded by [PHASES.md](docs/PHASES.md)).
- `docs/CRG_INSPIRATIONS.md` — design archaeology (CRG attribution retained in README).
- `docs/LEFTOVERS.md` — merged into [PHASES.md](docs/PHASES.md).

### Fixed
- `signing.py` no longer swallows all exceptions during Ed25519 verification.
- CI synthetic git repos hardened (`build_repo.py` fsync + git config); phase7 timing threshold relaxed for runners.
- SQLite edge decay uses bulk SQL + `busy_timeout`; context requests skip decay when the DB is locked.
- Co-change decay runs on a standalone writer connection (avoids pool read/write lock contention on large graphs).

## [0.1.0] - 2026-06-01

### Added
- Initial MCP server: git co-change graph, query-first context, eval harness, feedback loop.
