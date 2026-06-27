# Phase 15 — Codified context bridge

Inspired by [Codified Context (arXiv:2602.20478)](https://arxiv.org/html/2602.20478v1#S3):
**pareto-context-graph** ranks *code*; project rules/specs/agents are *knowledge about code*.
Phase 15 connects the two without turning PCG into a constitution/agent-spec product.

**North star:** right files (PCG) + right knowledge (rules/specs) + honest signals when either is missing.

---

## Phase map

| ID | Goal | Deliverable | Status |
|----|------|-------------|--------|
| **15.1** | Actionable gap signal on weak retrieval | `knowledge_gap` on `context` | shipped |
| **15.2** | Stale-spec warning | `spec_drift` in `doctor` + `context-map.json` | shipped |
| **15.3** | File/intent → specialist hints | `routing_hints` from `routing.json` | shipped |
| **15.4** | Workflow hook presets | `docs/examples/hooks/pre_change.py`, `post_change.py` | shipped |
| **15.5** | Spec/doc hybrid search | FTS index over `docs/`, rules, `AGENTS.md` | shipped |
| **15.6** | Subsystem map + MCP tools | `list_subsystems`, `subsystem_files` | shipped |
| **15.7** | Feedback → codify nudges | `codify_suggestion` in `feedback_hints` | shipped |
| **15.8** | Dual-layer response | `code_context` + `spec_context` fields | shipped |

---

## Repo-local config (optional)

### `.pareto-context-graph/context-map.json`

Maps subsystems to code globs and spec files for drift detection.

```json
{
  "subsystems": {
    "auth": {
      "path_globs": ["src/auth/**", "lib/auth/**"],
      "specs": ["docs/auth.md", ".cursor/rules/auth.mdc", "AGENTS.md"]
    }
  }
}
```

### `.pareto-context-graph/routing.json`

Routes retrieval signals to editor agents/rules (hints only — PCG does not launch agents).

```json
{
  "rules": [
    {
      "id": "openapi",
      "match": { "intent": "openapi" },
      "suggest": { "hint": "Load OpenAPI/security rules before editing routes." }
    },
    {
      "id": "high_hub",
      "match": { "hub_degree_gte": 500 },
      "suggest": { "hint": "Hub seed: tier=1, tight timeout_ms, then escalate." }
    }
  ]
}
```

---

## Acceptance (15.1–15.8)

- [x] `context` includes `knowledge_gap` when `retrieval_confidence.level` is `low`
- [x] `doctor` includes `spec_drift.warnings` when code changed without spec updates
- [x] `context` includes `routing_hints` when `routing.json` matches
- [x] Example pre/post-change hooks documented and copyable
- [x] Unit tests for gap, drift, routing
- [x] `include_specs=true` adds `spec_context.snippets` BM25 hits; `search` returns `spec_hits`
- [x] `list_subsystems` and `subsystem_files` MCP commands
- [x] `feedback_hints.codify_suggestion` when a path is rejected ≥3× in 7 days
- [x] `response_version: 3` with `code_context` + structured `spec_context`
- [x] Linux bench: `PCG_EDGE_DECAY=0` during `make bench-linux` (protects T3 graph)

---

## Linux benchmark snapshot (2026-06-26)

| Metric | Value |
|--------|--------|
| SHA | `840ef6c78e6a` |
| Files | 40,019 |
| Hub | `MAINTAINERS` |
| `graph.db` | ~80 MB |
| Hub-only p50 / p95 | ~594 ms / ~643 ms (`bench_results.json`) |
| Hub timeout tests | 3/3 pass |
| Full cold build | ~10.5 h (first complete build); decay can prune edges — use `PCG_EDGE_DECAY=0` on bench |

---

## Response shape (15.8)

`context` returns `response_version: 3`. Top-level v2 fields (`context_files`, `tier`, …) remain for compatibility. Layered fields:

```json
{
  "response_version": 3,
  "request_id": "…",
  "context_files": [ … ],
  "code_context": {
    "context_files": [ … ],
    "tier": 1,
    "tokens_used": 59,
    "files_included": 2,
    "files_available": 18
  },
  "spec_context": null,
  "routing_hints": [ … ],
  "knowledge_gap": null,
  "suggested_next": { … }
}
```

With `include_specs: true` and hits:

```json
"spec_context": {
  "count": 2,
  "snippets": [
    { "path": "docs/auth.md", "kind": "doc", "title": "…", "snippet": "…", "score": 12.4 }
  ]
}
```
