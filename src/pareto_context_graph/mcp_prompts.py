"""MCP prompt templates (CRG §5) — guided multi-step workflows."""

from __future__ import annotations

from typing import Any

PROMPT_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "code_review",
        "description": "Blast radius → tier-1 context → risk summary for changed files.",
        "arguments": [
            {"name": "files", "description": "Changed file paths (PR diff)", "required": True},
            {"name": "query", "description": "Review focus (optional)", "required": False},
        ],
    },
    {
        "name": "debug_issue",
        "description": "Search → neighbours → tier-1 context for a bug or failure.",
        "arguments": [
            {"name": "query", "description": "Error message or symptom", "required": True},
            {"name": "seed_file", "description": "Optional failing test or log path", "required": False},
        ],
    },
    {
        "name": "architecture_overview",
        "description": "Communities, hotspots, and architecture report pointers.",
        "arguments": [],
    },
    {
        "name": "onboard_file",
        "description": "Tier-1 context + neighbours for one file.",
        "arguments": [
            {"name": "file", "description": "Path to onboard", "required": True},
        ],
    },
    {
        "name": "pre_merge_check",
        "description": "detect_changes + blast + savings before merge.",
        "arguments": [
            {"name": "base", "description": "Git base branch (default main)", "required": False},
        ],
    },
]


def _fill(template: str, args: dict[str, Any]) -> str:
    out = template
    for key, value in args.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def render_prompt(name: str, arguments: dict[str, Any] | None = None) -> str:
    """Return user-message text for prompts/get."""
    args = dict(arguments or {})
    if name == "code_review":
        files = args.get("files") or args.get("file") or ""
        if isinstance(files, list):
            files = ", ".join(files)
        query = args.get("query") or "review risk and missing tests"
        return _fill(
            """You are reviewing code changes. Use pareto_context_graph in order:

1. `detect_changes` with base=main (or PR base) to list changed + affected files.
2. `blast` on: {{files}}
3. `context` with tier=1, files=[{{files}}], query="{{query}}", session_memory=false.
4. Summarize: blast size, hub files touched, missing tests, and suggested reviewers.

Do not read full file contents until tier-1 paths are ranked.""",
            {"files": files, "query": query},
        )
    if name == "debug_issue":
        seed = args.get("seed_file") or args.get("file") or ""
        query = args.get("query") or ""
        seed_line = f'files=["{seed}"], ' if seed else ""
        return _fill(
            """Debug this issue with pareto_context_graph:

1. `search` query="{{query}}" (limit 10)
2. `context` with {{seed_line}}query="{{query}}", tier=1, query_first=true, session_memory=false
3. For top 2 paths, re-call `context` tier=2 then tier=3 only if implementation detail is needed.
4. Use `neighbours` on the most relevant path if co-change partners are unclear.

Prefer graph-ranked files over repo-wide grep.""",
            {"query": query, "seed_line": seed_line},
        )
    if name == "architecture_overview":
        return """Map this codebase architecture:

1. `stats` and `doctor` for graph health.
2. `communities` for Leiden/connected clusters.
3. `hotspots` top_n=15 for churn hubs.
4. `architecture_report` (or read `.pareto-context-graph/ARCHITECTURE_REPORT.md`).
5. Summarize boundaries, hubs, and where to start for new contributors."""
    if name == "onboard_file":
        file = args.get("file") or args.get("path") or ""
        return _fill(
            """Onboard to file {{file}}:

1. `context` files=["{{file}}"], tier=1, session_memory=false
2. `neighbours` path="{{file}}"
3. `list_subsystems` + `subsystem_files` if subsystem map exists
4. Summarize role, key partners, and spec files from spec_context if present.""",
            {"file": file},
        )
    if name == "pre_merge_check":
        base = args.get("base") or "main"
        return _fill(
            """Pre-merge safety check:

1. `detect_changes` base={{base}}
2. `affected` base={{base}} (or paths=[...] from the diff) for test selection
3. If stale_index: run `update` first, then repeat detect_changes.
4. `savings` base={{base}} for token reduction vs naive read
5. Flag emerging_hubs and community_labels_touched; recommend extra review if blast_count > 20.""",
            {"base": base},
        )
    raise KeyError(f"Unknown prompt: {name}")
