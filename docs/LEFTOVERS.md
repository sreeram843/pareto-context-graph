# Phase leftovers

Tracked gaps and follow-up work across the execution plan. Update this file
when closing an item in [PHASES.md](PHASES.md).

**Phases 0–8:** largely complete. **Phases 9–14:** improvement roadmap — see
[PHASES.md § Phase 9–14](PHASES.md#phase-9--eval-quality-lift-2-weeks).

---

## Phase 0 — Bench setup

| Item | Status | Notes |
|------|--------|-------|
| T2 kubernetes clone + build on CI machine | **Done** | `.github/workflows/bench-t2.yml` (Sunday + manual) |
| T3 linux 100k-commit stress | **Done** | Build + latency recorded; `make bench-linux` |
| Pin kubernetes/linux SHAs in `pins.json` | **Done** | k8s `e62c2b04709`; linux `840ef6c78e6a` |

## Phase 1 — Eval + CI

| Item | Status | Notes |
|------|--------|-------|
| Grow fastapi golden set toward 50 cases | **Done** | 50 cases in `golden/fastapi/cases.json` |
| kubernetes golden `cases.json` | **Done** | 24 cases in `golden/kubernetes/cases.json` |
| P0 token reduction (adaptive cap, session memory, bench savings) | **Done** | `adaptive_cap.py`, `session.py`, bench `token_savings` |
| httpx in CI eval workflow | **Done** | Phase 9.4 — fastapi + httpx in `.github/workflows/eval.yml` |
| Regression limit documentation in PR template | **Done** | Phase 9.7 — eval-audit checklist |

## Phase 2 — Token honesty

| Item | Status | Notes |
|------|--------|-------|
| Default tokenizer to tiktoken in production | **Done** | Phase 12.1 — Docker `[tiktoken]`; `auto` prefers tiktoken when installed |
| Eval gate on all PRs touching packing | **Done** | Phase 12.5 — packing paths in `.github/workflows/eval.yml` |

## Phase 3 — Query-first retrieval

| Item | Status | Notes |
|------|--------|-------|
| `PCG_FEATURE_QUERY_FIRST` default on | **Done** | Default on; `PCG_FEATURE_QUERY_FIRST=0` to disable |
| `PCG_FEATURE_DIAGNOSTICS` default on | **Done** | Default on; env opt-out |
| tree-sitter symbol index (optional) | **Done** | Phase 11.1 — `PCG_FEATURE_TREESITTER=1` + `[treesitter]` extra |

## Phase 4 — Structure + savings

| Item | Status | Notes |
|------|--------|-------|
| `PCG_FEATURE_STRUCTURAL_EDGES` default on | **Done** | Default on for blast traversal |
| `PCG_FEATURE_LEIDEN` default on | **Done** | Default on; falls back without igraph |
| kubernetes community eval | **Done** | Phase 9.8 — 4 `category: community` cases |

## Phase 5 — Feedback + learned ranking

| Item | Status | Notes |
|------|--------|-------|
| Held-out MRR +3 pts after feedback replay | **Done** | `feedback_replay.py` + `eval --feedback-replay` |
| LambdaMART ranker (optional) | **Done** | `pip install -e '.[ranker]'`; `train_best_ranker` + CI logistic path |
| `feedback_dwell` client integration docs | **Done** | `docs/FEEDBACK.md` agent loop + dwell tracking |
| CI feedback-replay gate | **Done** | `.github/workflows/eval.yml` |
| Python `api.py` feedback helpers | **Done** | `feedback_*`, `learn()` on `ParetoContextGraph` |
| Nightly `learn` cron example | **Done** | `docs/FEEDBACK.md` |
| Learned prune from feedback | **Done** | Phase D — `prune_weights.json` + `learn` |
| Counterfactual replay vs grep baseline in CI | **Done** | `check_grep_counterfactual_gate` on `make eval-check` |

## Phase 6 — Huge-repo stress bench

| Item | Status | Notes |
|------|--------|-------|
| Real kubernetes bench numbers in BENCHMARKS.md | **Done** | 289 MB db; build 792s; hub-only p95 ~6 ms |
| Real linux T3 numbers | **Done** | 1.2 GB db; latency re-bench 2026-06-25 in BENCHMARKS.md |
| `tests/test_hub_timeout.py` on OSS hub file | **Done** | fastapi + kubernetes + linux (`MAINTAINERS`) |
| context p95 < 2s on kubernetes (target) | **Done** | Hub-only **0.006s**; with-query **0.050s** |
| context p95 < 5s on linux (T3 target) | **Done** | Hub-only **0.132s** p95 (2026-06-25); under 5 s target |

## Phase 7 — Operational hardening

| Item | Status | Notes |
|------|--------|-------|
| MCP `$/cancelRequest` handling | **Done** | `notifications/cancelled` + thread-local cancel event |
| Ed25519 snapshot signing | **Done** | Optional via `PCG_ED25519_KEY` + `cryptography` |
| Prometheus scrape HTTP endpoint | **Done** | `pareto-context-graph metrics --serve` → `/metrics`, `/traces` |
| OTel tracing (`plan → retrieve → rank → pack`) | **Done** | OTLP HTTP/gRPC via `OTEL_EXPORTER_OTLP_*`; in-process `/traces` buffer |
| Org policy YAML (`/etc/pareto-context-graph/policy.yaml`) | **Done** | Layered merge with `$PCG_POLICY` + `.pareto-context-graph/policy.json`; optional `pyyaml` |
| VS Code extension (one-click + feedback) | **Open → Phase 13.1–13.2** | `install --platform cursor` writes `.cursor/mcp.json` |
| Concurrency gate in CI | **Done** | Phase 14.2 — `test_phase7.py` in eval workflow |
| Linux build indexing throughput | **Open → Phase 10.7** | Cold build **37,877s** (~10.5 h); full re-bench pending |

## Phase 8 — In-house compression *(done)*

| Item | Status | Notes |
|------|--------|-------|
| `docs/CONTEXT_COMPRESSION.md` | **Done** | Prune, retrieve, learned prune, eval gates |
| Eval column: graph → compressed tokens | **Done** | `eval --compress-stack` + `compress_stack.py` |
| Phase C compress regression gate | **Done** | `make eval-compress-check` + `baseline-compress.json` |
| Learned prune from feedback (Phase D) | **Done** | `prune_learn.py` + `learn` → `prune_weights.json` |

---

## Phase 9 — Eval quality lift

| Item | Status | Notes |
|------|--------|-------|
| Fix 4 fastapi cases with `recall@5 = 0` | **Done** | Phase 9.1 |
| Portable baseline paths in `eval.py` | **Done** | `portable_eval_payload()` |
| `scripts/audit_golden_cases.py` | **Done** | Fails on any `recall@5 = 0` |
| PR-sourced golden cases | **Done** | Phase 9.2 |
| kubernetes ≥ 20 cases | **Done** | 24 cases + `baseline-kubernetes.json` |
| httpx in CI eval | **Done** | Phase 9.4 |
| kubernetes eval-check in CI | **Done** | Phase 9.5 — `bench-t2.yml` |
| PR template eval section | **Done** | Phase 9.7 |
| kubernetes community eval | **Done** | 4 `category: community` cases + `summary.by_category` |

## Phase 10 — Build + snapshots

| Item | Status | Notes |
|------|--------|-------|
| Linux build profiling (10.1) | **Done** | `build_profile` meta + `scripts/profile_build.py` |
| Build optimizations (10.2) | **Done** | Bulk `record_co_change`, SQL `top_neighbours` |
| Incremental build + index (10.3) | **Done** | Noop skip, commit-window cache, `index_state` |
| `doctor` build time estimate (10.4) | **Done** | `build_estimate.py` |
| CI snapshot publish (k8s, 10.5) | **Done** | Signed export in `bench-t2.yml` |
| Snapshot-first onboarding docs | **Done** | [CI_SNAPSHOTS.md](CI_SNAPSHOTS.md), BENCHMARK_REPOS, QUICKSTART, README |
| Linux cold build re-bench (10.7) | **Open** | Latency refreshed 2026-06-25; cold build pending |

## Phase 11 — Signals + pruning

| Item | Status | Notes |
|------|--------|-------|
| tree-sitter symbol index | **Done** | Phase 11.1 — `tests/test_fastapi_symbol_search.py` on bench/fastapi |
| Selective hybrid on large repos (query-only) | **Shipped** | `selective_hybrid.py` |
| Community-aware rank boost | **Shipped** | Phase 11.3 |
| SWE-Pruner post-pack summary prune | **Done** | `summary_prune.py` + `check_summary_prune_gate` (tail prune, protect top 10) |
| Ranker feedback features (11.5) | **Shipped** | `was_in_already_have`, `dwell_seconds`, `rejected` in `FEATURE_KEYS` |
| Learned tier-1 prune (11.6) | **Done** | `apply_learned_tier1_prune` + `check_learned_tier1_prune_gate` |
| Grep-baseline counterfactual CI | **Shipped** | `eval.py` + `make eval-check` |

## Phase 12 — Default token stack

| Item | Status | Notes |
|------|--------|-------|
| tiktoken default in Docker/install | **Done** | Docker `[tiktoken]`; install tip + QUICKSTART |
| `session clear` command | **Done** | CLI `session clear` + MCP `session_clear` |
| Tier + session hygiene in agent instructions | **Done** | `install` copilot-instructions template |
| Packing path filter in CI | **Done** | Phase 12.5 — `eval.yml` packing/budget paths |

## Phase 13 — Agent DX

| Item | Status | Notes |
|------|--------|-------|
| VS Code / Cursor extension | **Open** | Phase 13.1–13.2 |
| `suggested_next` in context response | **Done** | Default on every `context` response |
| Feedback hook examples | **Done** | `docs/examples/hooks/feedback_hints.py` + HOOKS.md |
| Org policy YAML | **Done** | `/etc/pareto-context-graph/policy.yaml` + merge; context knobs via `apply_context_policy` |

## Phase 14 — Fleet observability

| Item | Status | Notes |
|------|--------|-------|
| OTel OTLP export | **Done** | `tracing.py` + `docker-compose.yml` collector example |
| Pool concurrency in CI | **Done** | `test_phase7.py` in eval workflow |
| Phase latency histogram metrics | **Done** | `ContextPhaseTracker` → `cgmcp_context_phase_latency_seconds` |
| Audit log rotation | **Done** | `audit.py` — 10 MiB × 5 files default; `policy.json` + env overrides |
| Snapshot verify in CI | **Done** | `bench-t2.yml` HMAC verify when key set |

---

## Suggested next actions

1. **Phase 10.7** — Full cold linux re-bench (`make bench-linux`, ~10+ h locally) if build numbers need refresh.
