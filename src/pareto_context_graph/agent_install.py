"""Multi-agent MCP install, steering markers, and uninstall."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PCG_MARKER_START = "<!-- PCG:START -->"
PCG_MARKER_END = "<!-- PCG:END -->"

INSTALL_TARGETS = frozenset(
    {"auto", "all", "cursor", "copilot", "vscode", "claude", "codex", "gemini", "windsurf"}
)
MCP_TARGETS = frozenset({"cursor", "copilot", "vscode", "claude", "codex", "gemini", "windsurf", "all", "auto"})


def mcp_server_entry(repo: Path, *, watch: bool = False) -> dict[str, Any]:
    args = ["serve", "--repo", str(repo.resolve())]
    if watch:
        args.append("--watch")
    return {"command": "pareto-context-graph", "args": args}


def steering_markdown() -> str:
    return f"""{PCG_MARKER_START}
# Pareto Context Graph — agent steering

Subagents do not see MCP `initialize` instructions. Follow these rules in this repo:

1. **Discovery:** `explore` or `context` (tier=1) before repo-wide grep/Read.
2. **PR / review:** `detect_changes` then `affected` for blast radius + test selection.
3. **Escalation:** tier=2 signatures, tier=3 chunks — only on paths from `suggested_next`.
4. **Freshness:** heed staleness banners; Read pending files directly.

Install tiktoken for honest budgets: `pip install -e '.[tiktoken]'`.
{PCG_MARKER_END}
"""


def copilot_instructions_markdown() -> str:
    return f"""{PCG_MARKER_START}
# Pareto Context Graph — Context Instructions

Before answering or editing, call the `pareto_context_graph` MCP tool:

- **Vague questions:** `{{"command": "explore", "query": "…"}}` (tier 1 default)
- **Known files:** `{{"command": "context", "files": ["path"], "query": "…", "tier": 1}}`
- **PR review:** `detect_changes` → `affected` → escalate tier only on ranked paths
- **New task:** `session_clear` so stale session paths are not merged

Do not grep the repo to discover context when `explore` can answer in one call.
{PCG_MARKER_END}
"""


def _resolve_targets(target: str) -> list[str]:
    if target in {"all", "auto"}:
        return ["cursor", "copilot", "claude"]
    if target == "vscode":
        return ["copilot"]
    return [target]


def _cursor_path(repo: Path, location: str) -> Path:
    if location == "global":
        return Path.home() / ".cursor" / "mcp.json"
    return repo / ".cursor" / "mcp.json"


def _copilot_path(repo: Path) -> Path:
    return repo / ".vscode" / "mcp.json"


def _merge_mcp_json(path: Path, entry: dict[str, Any], *, servers_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    servers = data.get(servers_key, {})
    servers["pareto-context-graph"] = entry
    data[servers_key] = servers
    path.write_text(json.dumps(data, indent=2) + "\n")


def _remove_mcp_json(path: Path, *, servers_key: str) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return False
    servers = data.get(servers_key, {})
    if "pareto-context-graph" not in servers:
        return False
    del servers["pareto-context-graph"]
    data[servers_key] = servers
    path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def _write_marked_block(path: Path, block: str, *, force: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text()
        if PCG_MARKER_START in text and PCG_MARKER_END in text:
            if not force:
                return f"already present in {path}"
            start = text.index(PCG_MARKER_START)
            end = text.index(PCG_MARKER_END) + len(PCG_MARKER_END)
            path.write_text(text[:start] + block.strip() + "\n" + text[end:].lstrip("\n"))
            return f"updated {path}"
        path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n")
        return f"appended to {path}"
    path.write_text(block.strip() + "\n")
    return f"wrote {path}"


def _remove_marked_block(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    if PCG_MARKER_START not in text or PCG_MARKER_END not in text:
        return False
    start = text.index(PCG_MARKER_START)
    end = text.index(PCG_MARKER_END) + len(PCG_MARKER_END)
    path.write_text((text[:start] + text[end:]).strip() + "\n")
    return True


def print_agent_config(repo: Path, target: str, *, location: str = "local", watch: bool = False) -> dict[str, Any]:
    entry = mcp_server_entry(repo, watch=watch)
    if target == "cursor":
        return {"mcpServers": {"pareto-context-graph": entry}}
    if target in {"copilot", "vscode"}:
        return {"servers": {"pareto-context-graph": {**entry, "type": "stdio"}}}
    if target == "claude":
        return {"mcpServers": {"pareto-context-graph": entry}}
    if target == "windsurf":
        return {"mcpServers": {"pareto-context-graph": entry}}
    if target == "codex":
        return {"mcp": {"servers": {"pareto-context-graph": entry}}}
    if target == "gemini":
        return {"mcpServers": {"pareto-context-graph": entry}}
    raise ValueError(f"unknown agent target: {target}")


def install_agent(
    repo: Path,
    target: str,
    *,
    location: str = "local",
    force: bool = False,
    watch: bool = False,
) -> list[str]:
    messages: list[str] = []
    entry = mcp_server_entry(repo, watch=watch)
    for name in _resolve_targets(target):
        if name == "cursor":
            path = _cursor_path(repo, location)
            _merge_mcp_json(path, entry, servers_key="mcpServers")
            messages.append(f"Cursor MCP config written to {path}")
        elif name == "copilot":
            path = _copilot_path(repo)
            _merge_mcp_json(path, {**entry, "type": "stdio"}, servers_key="servers")
            messages.append(f"Copilot MCP config written to {path}")
        elif name == "claude":
            messages.append(
                "Claude: merge print-config output into your Claude Desktop / Code MCP settings"
            )

    messages.append(_write_marked_block(repo / "AGENTS.md", steering_markdown(), force=force))
    messages.append(
        _write_marked_block(repo / ".cursor/rules/pcg.mdc", steering_markdown(), force=force)
    )
    messages.append(
        _write_marked_block(
            repo / ".github/copilot-instructions.md",
            copilot_instructions_markdown(),
            force=force,
        )
    )
    return messages


def uninstall_agent(
    repo: Path,
    target: str,
    *,
    location: str = "local",
) -> list[str]:
    messages: list[str] = []
    for name in _resolve_targets(target):
        if name == "cursor":
            path = _cursor_path(repo, location)
            if _remove_mcp_json(path, servers_key="mcpServers"):
                messages.append(f"removed Cursor MCP entry from {path}")
        elif name == "copilot":
            path = _copilot_path(repo)
            if _remove_mcp_json(path, servers_key="servers"):
                messages.append(f"removed Copilot MCP entry from {path}")

    for path in (repo / "AGENTS.md", repo / ".cursor/rules/pcg.mdc", repo / ".github/copilot-instructions.md"):
        if _remove_marked_block(path):
            messages.append(f"removed steering markers from {path}")
    return messages
