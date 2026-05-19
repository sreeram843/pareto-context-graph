# Production Roadmap

Handoff document for taking `code-graph-mcp` from prototype to a tool that serves
~1000 engineers across multiple large repositories. Each phase is self-contained
and can be assigned to a different agent / engineer. Read [Conventions](#conventions)
and [Prerequisites](#prerequisites-from-the-owner) before picking up any task.

---

## Why this exists

A live trial on `telapp` with the question *"How does Axle integration work?"*
revealed four reproducible failures and five systemic gaps. The full root-cause
analysis is in chat history; the short version:

| ID | Failure | Root cause | Phase that fixes it |
|----|---------|------------|---------------------|
| A | `search("axle")` finds nothing useful | Path-only FTS5 in [src/code_graph_mcp/store.py](../src/code_graph_mcp/store.py) `search_files` | Phase 3 |
| B | `context` cannot answer a question without seed files | [src/code_graph_mcp/server.py](../src/code_graph_mcp/server.py) requires non-empty `files` | Phase 3 |
| C | Hub-seeded `context` calls time out | No per-phase deadlines or I/O cap in [src/code_graph_mcp/server.py](../src/code_graph_mcp/server.py) | Phase 5 |
| D | `tokens_used` overshoots `token_budget` | Budget enforced on estimate, reported on payload size in [src/code_graph_mcp/server.py](../src/code_graph_mcp/server.py); `BYTES_PER_TOKEN` heuristic in [src/code_graph_mcp/tokens.py](../src/code_graph_mcp/tokens.py) | Phase 2 |
| S1 | Feedback signal is mostly negative | `log_feedback(..., used=False)` for every result; `mark_used` rarely called | Phase 4 |
| S2 | SQLite write contention at scale | Per-call commits in [src/code_graph_mcp/store.py](../src/code_graph_mcp/store.py); shared DB with `serve --watch` | Phase 5 |
| S3 | Unsigned hooks + snapshot URLs | [src/code_graph_mcp/hooks.py](../src/code_graph_mcp/hooks.py), [src/code_graph_mcp/snapshot.py](../src/code_graph_mcp/snapshot.py) | Phase 5 |
| S4 | No real eval suite | [tests/eval/cases.json](../tests/eval/cases.json) has 3 cases | Phase 1 |
| S5 | No retrieval transparency | Response carries no per-candidate scores or rejection reasons | Phase 3 + Phase 4 |

---

## Conventions

These apply to **every** task.

- **Branching**: one branch per task: `phase<N>/<task-slug>`. PRs target `main`.
- **Feature flags**: every behavioural change ships behind an env var or
  request-level flag. Default off until eval clears it. Flag naming:
  `CGMCP_FEATURE_<NAME>`.
- **Eval gate**: PRs that touch retrieval or ranking **must** report eval
  metrics (delta vs. baseline) in the PR description. CI fails if
  `recall@5`, `MRR`, or `nDCG@10` regress by more than 2 absolute points
  on the golden set without explicit override.
- **Backward compatibility policy**:
  - Pre-GA (no external clients): allow one schema cleanup pass to produce a
    stable `response_version: 2` shape.
  - Post-GA (once clients integrate): additive-only changes for `v2.x`.
  - Any future breaking change requires a major version (`v3`), migration
    notes, and a temporary compatibility adapter.
- **Tests**: every task adds unit tests and at least one integration test
  that drives the change through the MCP stdio path.
- **No new runtime dependency without sign-off.** The project advertises
  zero-deps. Phase 3 and Phase 4 each request one dependency; both are
  itemised below and require owner approval (see Prerequisites).
- **Definition of done**: code merged + tests green + eval delta reported
  + flag default decided + docs updated.

---

## Prerequisites from the owner

The following decisions and access items are blocking. Without them, work
either cannot start or will need to be redone.

### Decisions (need explicit answers)

1. **Embedding backend** for Phase 3 / Phase 4. Pick one:
   - Ollama with a local code embedding model (default; zero-cost; needs Ollama installed by devs or via Docker sidecar in CI).
   - OpenAI `text-embedding-3-small` (paid; needs corporate key + quota).
   - Internal Teladoc-hosted model (preferred long term; needs endpoint + auth).
2. **Tokenizer target** for Phase 2. Pick one or several:
   - `tiktoken` `cl100k_base` (GPT-4 family).
   - `tiktoken` `o200k_base` (GPT-4o / o-series).
   - Per-model selection by client (most accurate; more work).
3. **Repos in the golden set** for Phase 1. `telapp` is in.
   Confirm which other repos to add. Suggested: 2–4 active services.
4. **Snapshot distribution**: internal artifact store URL + auth model
   (Artifactory? S3? GitHub Releases on an internal org?).
5. **Signing key custody**: who owns the snapshot signing key, and what
   does rotation look like.
6. **Telemetry endpoint**: Prometheus push gateway? OTel collector?
   Datadog? Decide once for the org.
7. **Client side**: do we control the VS Code extension that ships
   `mcp.json`? Phase 4 implicit feedback (`view`, `cite`) depends on
   client cooperation.
8. **Wire format**: is breaking the response shape acceptable in a major
   version, or must Phase 2/3 additions all be additive forever?

### Decisions confirmed (2026-05-16)

1. **Repos in golden set (initial)**: `telapp`.
2. **`tiktoken` dependency**: **yes**.
3. **`tree-sitter` dependency**: **yes**.
4. **Embedding backend**: **Ollama**.
5. **Wire format direction**:
   - Because this is a net-new repo with no clients, we will take the best
     schema design now and ship a clean `response_version: 2` output.
   - For later phases and after adoption, we will preserve backward
     compatibility (additive-only in `v2.x`).
   - If a future break is needed, do it only as an explicit major version
     with migration docs and an adapter window.

### Access / artifacts needed

- Read access to git history of every repo we add to the golden set
  (local clones are enough; PR titles + diffs preferred for richer
  ground truth).
- Internal PyPI credentials (publish-only) for release tasks.
- A non-prod artifact store path for testing snapshot signing end-to-end.
- An audit-log sink (object store or log pipeline) for Phase 5.

### Acknowledgements (need a yes/no)

- Adding `tree-sitter` (Phase 3) — yes / no.
- Adding `tiktoken` (Phase 2) — yes / no.
- Adding `lightgbm` (Phase 4) — yes / no. (Alternative: pure-python
  logistic regression on per-feature scores; lower ceiling.)

---

## Phase 1 — Eval harness *(unblocks everything else)*

Goal: a regression gate strong enough that we can change retrieval and
ranking without flying blind.

### Task 1.1 Golden set v1
- Build a structured golden set of 50–100 questions per repo.
- For each: `{repo, question, intent, relevant_files[], notes}`.
- Source from real PRs (`git log --name-only` for files actually touched)
  and from at least 5 incident postmortems per repo.
- Store under `tests/eval/golden/<repo>/`.
- Acceptance: ≥ 50 cases for `telapp`; schema documented in
  `tests/eval/README.md`.

### Task 1.2 Metrics module
- Implement `recall@k`, `MRR`, `nDCG@k`, plus two new metrics:
  - `token_efficiency = relevant_tokens / tokens_used`.
  - `budget_honesty   = 1 − |reported − actual| / budget`.
- Location: `src/code_graph_mcp/eval.py` (extend; do not break existing).
- Acceptance: unit tests on synthetic cases; deterministic outputs.

### Task 1.3 CI integration
- New Make target `make eval` runs all repos in the golden set, prints a
  table, exits non-zero on regression > 2 points.
- GitHub Actions job that runs `make eval` on PRs that touch retrieval
  paths (allowlist by path glob).
- Acceptance: PR template prompts the contributor to paste the eval
  diff; CI enforces the gate.

### Task 1.4 Baseline snapshot
- Run the suite on `main` as it is today; commit the JSON results as
  `tests/eval/baseline.json`.
- Every subsequent PR compares to this file; `make eval --update-baseline`
  refreshes it intentionally.

---

## Phase 2 — Token honesty

Goal: when the API says `token_budget = X`, the response uses ≤ X tokens
of the client's tokenizer, and `tokens_used` reflects reality.

### Task 2.1 Pluggable tokenizer
- New module `src/code_graph_mcp/tokenizer.py` with an interface:
  ```python
  class Tokenizer(Protocol):
      def count(self, text: str) -> int: ...
  ```
- Implementations: `BytesPerTokenTokenizer` (legacy, default off),
  `TiktokenTokenizer(encoding="cl100k_base"|"o200k_base")`.
- Selection: request arg `tokenizer` on `context`, fall back to env
  `CGMCP_TOKENIZER`, fall back to legacy.
- Acceptance: unit tests against canned strings; documented overhead
  budget (≤ 2 ms / 1KB).

### Task 2.2 Incremental packing
- Replace Phase 5 of `_handle_tool_call` `context` branch in
  [src/code_graph_mcp/server.py](../src/code_graph_mcp/server.py).
- For each candidate, build its entry, measure tokens against the
  selected tokenizer, accept only if `tokens_used + entry ≤ budget`.
- Stop on first rejection; do **not** mutate `tokens_used` at the end.
- Acceptance: `budget_honesty ≥ 0.95` on the eval set. No response
  exceeds `token_budget`.

### Task 2.3 Per-entry token reporting
- Each entry in `context_files` carries `tokens_actual`.
- Response includes `dropped_candidates` (count + first 10 paths).
- Acceptance: clients can request more by calling again with
  `already_have` and a larger budget.

### Task 2.4 Compression knob
- Add `compression` request arg: `"none" | "lossy"`.
- `lossy` allows tier-2 to truncate low-importance signatures (initial
  heuristic: drop private/internal methods first; replaced by learned
  importance in Phase 4).
- Acceptance: at tight budgets, `recall@5` does not regress vs. `none`.

---

## Phase 3 — Query-first context + multi-signal retrieval

Goal: a question alone (no `files`) returns the right files. `search`
finds concept matches, not just paths.

### Task 3.1 Symbol index (Tree-sitter)
- Add `tree-sitter` dependency (requires owner sign-off).
- At build time, parse files for languages we care about (Ruby, Python,
  JS/TS, Go, Java, Rust, SQL). For each definition emit
  `(symbol, kind, file, line, container_path)`.
- Store in new SQLite table `symbols` with an FTS5 index over `symbol`
  tokenised on camelCase and snake_case.
- Acceptance: `search("AxleVisitCreator")` returns the defining file
  even if no path contains "axle".

### Task 3.2 BM25 content index
- Replace `KeywordIndex` TF-IDF in [src/code_graph_mcp/chunks.py](../src/code_graph_mcp/chunks.py)
  with a BM25 inverted index over file contents.
- Build incrementally during `build` / `update`; store in SQLite.
- Acceptance: micro-bench shows ≥ 5× faster query, equal or better
  recall on the eval set.

### Task 3.3 RRF fusion + new retriever API
- New module `src/code_graph_mcp/retrievers.py`:
  - `PathRetriever`, `SymbolRetriever`, `BM25Retriever`, `EmbedRetriever`,
    `CoChangeRetriever`. Each returns ranked `(path, score)` tuples.
- New module `src/code_graph_mcp/orchestrator.py`:
  - `plan(query)` → intent + retriever weights.
  - `retrieve(query, files)` → fused candidate pool via Reciprocal Rank
    Fusion (`k=60`).
- Acceptance: unit tests on each retriever in isolation; eval improves
  on concept-queries category.

### Task 3.4 Query-first `context`
- Drop the hard `files`-required check in `context`.
- When `files` is empty, call `orchestrator.retrieve(query, [])` and use
  the top-K as virtual seeds.
- When `files` is provided, current behaviour with seeds.
- Behind flag `CGMCP_FEATURE_QUERY_FIRST` initially.
- Acceptance: `context` with only `query="How does Axle integration work?"`
  surfaces ≥ 3 of the curated Axle ground-truth files in the top 10.

### Task 3.5 Diagnostics mode
- New request arg `diagnostics: true`.
- Response includes for each returned and rejected candidate:
  - per-feature scores (`co_change`, `bm25`, `symbol`, `embed`,
    `locality`, `hub_penalty`, `learned_boost`)
  - final ranker score
  - rejection reason if dropped.
- Acceptance: a single failing case is debuggable from the response
  alone, without running with `--verbose`.

---

## Phase 4 — Learned ranking + working feedback loop

Goal: ranking improves over time from real usage. Feedback is not all
negative.

### Task 4.1 Feedback event log
- New append-only file `.code-graph/events.jsonl` for: `view`, `cite`,
  `accept`, `reject`, `dwell`.
- New MCP commands: `feedback_view`, `feedback_cite`, `feedback_accept`,
  `feedback_reject`. Idempotent on `(request_id, path)`.
- Batched fold into the `feedback` SQLite table every 30 s by a
  background worker thread.
- Acceptance: 1 KQPS sustained write throughput in micro-bench; no
  contention with `serve --watch`.

### Task 4.2 Counterfactual logging
- Every `context` request logs the full ranked candidate pool with
  per-feature scores into `events.jsonl` (gzipped daily).
- Acceptance: offline replay can reconstruct the request → ranking.

### Task 4.3 Learned re-ranker
- New module `src/code_graph_mcp/ranker.py`.
- Train a LambdaMART model nightly from `events.jsonl`:
  - Positives: `cite`, `accept`, `dwell ≥ 30s`.
  - Negatives: `reject`, `view` without follow-up.
- Inference at request time: score the fused candidate pool, blend with
  prior `α·learned + (1−α)·prior`. `α` grows with sample size.
- Acceptance: eval `MRR` improves by ≥ 3 points on the held-out split.

### Task 4.4 Per-feature scores everywhere
- Phase 3 already records features. Persist them in the candidate
  payload so the ranker can be retrained without re-running retrieval.

---

## Phase 5 — Operational hardening

Goal: this tool can be shipped to 1000 devs and run unattended.

### Task 5.1 WAL + read pool
- Open SQLite with `journal_mode=WAL`, `synchronous=NORMAL`.
- Per-process connection pool (size = `os.cpu_count()`), readers vs.
  single writer.
- Acceptance: concurrency test with 32 readers + 1 writer shows no
  errors and < 5 ms p99 read latency.

### Task 5.2 Per-phase deadlines + cancellation
- Each phase of `context` takes a deadline from a request-level
  `timeout_ms` (default 5000).
- Honour MCP `$/cancelRequest`.
- Cap symbol extraction at 200 file reads per request; everything else
  must come from the precomputed symbol index.
- Acceptance: hub-seeded query on `tas/app/models/consultation.rb`
  returns within 5 s with a partial result and a `truncated: true` flag.

### Task 5.3 Signed snapshots
- `snapshot export` writes a signature file alongside the tarball,
  using an org-managed Ed25519 key.
- `snapshot import` verifies the signature and refuses unsigned
  snapshots when `CGMCP_REQUIRE_SIGNED_SNAPSHOTS=1`.
- Acceptance: tampered snapshot is refused; signed snapshot loads.

### Task 5.4 Hook allowlist
- Hooks load only if their SHA-256 appears in an org policy file
  (`/etc/code-graph/policy.yaml` or `$CGMCP_POLICY`).
- `no_safety` requires the policy to set `allow_no_safety: true`.
- Acceptance: arbitrary hook in `.code-graph/hooks/` is ignored unless
  hashed in policy.

### Task 5.5 Audit log
- Append-only audit record per `context` / `search` call:
  `(ts, user, repo, query_hash, returned_paths_count, tokens_used)`.
- Sink configurable (file by default; pluggable shipper).
- Acceptance: log replays into a query-volume dashboard.

### Task 5.6 Telemetry
- Prometheus metrics: `cgmcp_request_latency_seconds{phase}`,
  `cgmcp_retriever_hits_total{retriever}`,
  `cgmcp_token_budget_overshoot_ratio`,
  `cgmcp_cache_hit_ratio`,
  `cgmcp_feedback_events_total{kind}`.
- OTel tracing across `plan → retrieve → rank → pack`.
- Acceptance: scraping endpoint or push works against the chosen sink
  from Prerequisites.

### Task 5.7 Org policy + VS Code extension
- Single org policy file controlling: profile defaults, allowed hook
  hashes, allowed snapshot sources, telemetry endpoint, redaction
  rules.
- VS Code extension that pins server version, writes `mcp.json` from
  policy, and forwards client-side feedback events.
- Acceptance: a fresh laptop with extension installed gets a working,
  policy-compliant setup with one click.

---

## Cross-phase order of operations

1. **Phase 1 lands first.** Without the eval gate, Phases 2–4 are
   guessing.
2. **Phase 2 lands second.** Cheap, isolated, large user-visible win.
3. **Phase 3 lands behind a flag.** Eval must show net positive before
   the flag flips on for default profiles.
4. **Phase 4 starts logging immediately** (Tasks 4.1, 4.2) as soon as
   Phase 3 ships, even before the re-ranker is trained, so we have data.
5. **Phase 5 can parallelise with 3 and 4** for everything except items
   that depend on the orchestrator (`telemetry` for `phase` labels).

---

## Per-task handoff template

When assigning a task to an agent, paste this block populated:

```
Task:          <e.g. Task 3.4 Query-first context>
Phase:         <N>
Branch:        phase<N>/<task-slug>
Goal:          <one sentence>
Files to touch:
  - <path>
Files to read first:
  - <path>
Acceptance criteria:
  - <bullet>
Feature flag:  CGMCP_FEATURE_<NAME> (default off)
Eval impact:   <which metric, expected direction>
Risks:         <bullet>
Out of scope:  <bullet>
```

---

## Open questions to revisit after Phase 1

- Do we replace the single-tool `code_graph` schema with named tools
  per command? Single-tool saves prompt tokens; named tools improve
  client-side validation.
- Should we support multi-repo queries in one call (cross-service
  questions)?
- Do we offer a hosted variant or stay strictly stdio per-laptop?
