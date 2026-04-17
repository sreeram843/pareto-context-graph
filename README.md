# code-graph-mcp

MCP server for repository knowledge-graph workflows (change impact, code exploration, and review context) designed for internal engineering use.

## Why This Exists

Engineering teams spend significant time answering the same questions during development and review:

- What changed and what is impacted?
- Which tests should be run?
- What files/functions depend on this symbol?
- What review risks should we prioritize?

This project provides graph-powered MCP tools so those questions can be answered quickly and consistently by humans and AI assistants.

It also improves token efficiency for AI-assisted workflows by returning targeted graph results instead of repeatedly scanning large files.

## What We Are Building

- A local-first graph index for repository structure and relationships.
- MCP tools for code search, dependency tracing, impact analysis, and review context.
- CLI workflows for graph lifecycle: build, update, status, and diagnostics.
- Safe defaults for enterprise use: predictable outputs, observability, and access controls.

## Expected Benefits

- Better query quality with focused, relationship-aware answers.
- Lower token usage in daily assistant workflows by reducing broad file reads.
- Faster iteration for development, debugging, and code review.

## Project Plan

See [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) for:

- Scope and non-goals
- Phased roadmap
- Success metrics
- Risks and mitigations
- MVP acceptance criteria

## Initial MVP Tool Set

- `detect_changes`: summarize changed symbols, risk signals, and test gaps.
- `query_graph`: query relationships (callers, callees, imports, tests).
- `semantic_search_nodes`: locate relevant symbols by meaning.
- `get_impact_radius`: identify downstream impact of a change.

## Development Principles

- Fast incremental indexing over full rebuilds.
- Deterministic tool responses with source attribution.
- Keep tool contracts small and composable.
- Optimize for developer workflows before dashboards.

## Next Step

Implement the repository skeleton described in [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) and deliver Phase 1 (MVP foundation).