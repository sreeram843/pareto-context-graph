"""MCP initialize instructions — agent playbook for pareto-context-graph."""

from __future__ import annotations

from pathlib import Path

from .store import DB_DIR, DB_NAME

SERVER_INSTRUCTIONS = """# Pareto Context Graph — git co-change intelligence under a token budget

PCG ranks **which files matter for this task** from git co-change history, query
fusion, and feedback — not from AST call graphs. One `context` or `explore` call
returns tier-1 summaries (default), tier-2 signatures, or tier-3 chunks within a
token budget, plus proof paths and suggested next steps.

## Primary tools — use before grep/Read loops

- **`explore`** or **`context`** — vague questions, architecture, "what touches X",
  or task context without seed files (query-first). Start at **tier=1**; escalate
  tier=2/3 only when you need API shape or verbatim code.
- **`detect_changes`** — PR/review: git diff + co-change blast radius + staleness.
- **`affected`** — PR/CI: which tests to run for a diff (reverse import walk).
- **`search`** — find candidate files/symbols when you lack a seed path.
- **`neighbours`** — direct co-change lookup for a known file.
- **`retrieve`** — verbatim payload by `content_hash` after prune/aggressive compression.

## Workflow

1. **`explore`** / **`context`** with your question (or seed `files` + `query`).
2. Trust ranked paths and tier-1 summaries; do not re-grep to "verify" co-change ranks.
3. Escalate **`tier=2`** only for signatures on paths from `suggested_next`.
4. Use **`retrieve`** or **`tier=3`** only when editing a specific ranked file.
5. After edits, heed the **staleness banner** — pending files need a direct Read.

## Anti-patterns

- Don't run grep+Read across the repo when **`explore`** can answer in one call.
- Don't default to tier=3 — it burns budget; tier=1→2 escalation is intentional.
- Don't ignore staleness banners for files listed as pending index sync.
- Don't run **`build`** yourself unless the user asks — suggest `pareto-context-graph build`.

## Limits

- Co-change reflects **history**, not static call edges (complements AST tools, doesn't replace them).
- Search/symbol index may lag writes ~1–2s when `--watch` is enabled; otherwise run `index`.
- No graph yet? Ask the user to run `pareto-context-graph build` in the repo root.
"""

SERVER_INSTRUCTIONS_NO_GRAPH = """# Pareto Context Graph — not indexed in this repo yet

PCG provides git co-change ranked context under a token budget via MCP commands
`explore`, `context`, `search`, and `detect_changes`.

There is no `.pareto-context-graph/graph.db` here yet. Use built-in Read/Grep for
this repo until the user runs `pareto-context-graph build`. Do not run build
yourself unless they ask.

Once indexed, prefer **`explore`** over ad-hoc file reads for structural questions.
"""


def graph_db_exists(repo_root: Path) -> bool:
    return (repo_root / DB_DIR / DB_NAME).exists()


def build_server_instructions(repo_root: Path) -> str:
    if graph_db_exists(repo_root):
        return SERVER_INSTRUCTIONS
    return SERVER_INSTRUCTIONS_NO_GRAPH
