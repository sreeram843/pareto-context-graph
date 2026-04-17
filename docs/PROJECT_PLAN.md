# Project Plan: code-graph-mcp

## 1. Objective

Build an internal MCP server that provides graph-based code intelligence for daily engineering tasks:

- change impact analysis
- review risk detection
- relationship traversal (callers, callees, imports, tests)
- context-aware code discovery

## 2. What This Helps With

### Developer Productivity

- Faster root-cause analysis for bugs and regressions.
- Faster identification of affected code paths and tests.
- Reduced context-switching during feature development.
- Lower token consumption for AI-assisted workflows by preferring targeted graph queries over full-file scans.

### Review Quality

- Prioritized review findings using impact and risk signals.
- Better test-gap visibility before PR merge.
- Consistent review depth across teams.

### Organizational Consistency

- Standardized graph-based workflows across repositories.
- Repeatable MCP tool contracts for assistant integrations.
- Shared platform for future policy and standards checks.

## 3. Scope

### In Scope (MVP)

- Repository indexing and graph persistence.
- Incremental updates from changed files.
- MCP server (stdio transport) exposing core tools.
- CLI commands for build, update, status, and detect-changes.
- Basic quality checks and logs.

### Out of Scope (MVP)

- Multi-repository federation.
- Hosted UI/dashboard.
- Advanced ML reranking pipelines.
- Automated code modifications.

## 4. Success Metrics

- p50 tool response time under 2 seconds for common queries.
- Incremental update time under 30 seconds for typical PR diffs.
- At least 80% positive pilot feedback from first team.
- Measurable reduction in manual dependency tracing during reviews.
- Measurable reduction in tokens consumed for representative review/debug prompts versus baseline grep/read workflows.

## 5. Risks and Mitigations

- Parser coverage gaps across languages:
  - Start with highest-priority languages and add adapters incrementally.
- Stale graph data:
  - Enforce update hooks and expose freshness in status output.
- Noisy or low-confidence output:
  - Include confidence score and source metadata in responses.
- Adoption friction:
  - Provide a minimal CLI + editor setup guide and examples.

## 6. Phased Roadmap

### Phase 0: Foundation (Week 1)

- Project scaffolding and architecture decisions.
- Graph schema design.
- CLI skeleton (`build`, `update`, `status`).

### Phase 1: MVP Core (Week 2-3)

- File parsing and graph population pipeline.
- Incremental update engine.
- MCP tools:
  - `detect_changes`
  - `query_graph`
  - `semantic_search_nodes`
  - `get_impact_radius`

### Phase 2: Workflow Quality (Week 4)

- Improve risk and test-gap heuristics.
- Add `get_review_context` and `get_affected_flows`.
- Add benchmarks and regression checks.

### Phase 3: Pilot and Hardening (Week 5-6)

- Pilot with one product team.
- Collect telemetry and feedback.
- Tune ranking, response shape, and defaults.

## 7. MVP Acceptance Criteria

- End-to-end flow works on one production repository:
  1. build graph
  2. update after code edits
  3. run detect-changes against base branch
  4. query dependencies and affected tests
- Tool outputs are deterministic and source-attributed.
- Team can install and use from local setup docs.

## 8. Immediate Next Actions

1. Finalize language support for MVP (start with Ruby + JavaScript/TypeScript if needed).
2. Decide graph backend (SQLite/duckdb + edge tables is sufficient for MVP).
3. Implement CLI command contracts first, then MCP wrappers.
4. Add setup docs for local usage and editor integration.
5. Pilot on one active branch workflow and measure baseline metrics.
