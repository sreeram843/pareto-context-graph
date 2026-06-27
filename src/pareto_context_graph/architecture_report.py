"""Deterministic architecture summary (Graphify-style report, no LLM)."""

from __future__ import annotations

from pathlib import Path

from .community import detect_communities
from .features import feature_enabled
from .profiles import autodetect_profile
from .store import Store


def build_architecture_report(repo_root: Path, *, top_hubs: int = 10, top_communities: int = 8) -> str:
    """Markdown report from graph stats, communities, and hubs."""
    store = Store(repo_root)
    try:
        stats = store.graph_stats()
        profile = autodetect_profile(repo_root) or "medium"
        comm = detect_communities(
            store,
            profile_name=profile,
            use_leiden=feature_enabled("LEIDEN"),
        )
    finally:
        store.close()

    lines = [
        f"# Architecture report — {repo_root.name}",
        "",
        "## Graph summary",
        "",
        f"- **Files:** {stats.get('files', 0)}",
        f"- **Co-change edges:** {stats.get('edges', 0)}",
        f"- **P95 degree:** {stats.get('p95_degree', 0)}",
        f"- **Profile:** {profile}",
        f"- **Community method:** {comm.get('method', 'unknown')}",
        "",
        "## Top hubs (high co-change degree)",
        "",
    ]
    for hub in (stats.get("top_hubs") or [])[:top_hubs]:
        lines.append(f"- `{hub['path']}` — degree {hub['degree']}")
    if not stats.get("top_hubs"):
        lines.append("- _(none)_")

    lines.extend(["", "## Architectural communities", ""])
    for block in (comm.get("communities") or [])[:top_communities]:
        label = block.get("label", "community")
        size = block.get("size", len(block.get("files") or []))
        sample = (block.get("files") or [])[:5]
        sample_txt = ", ".join(f"`{p}`" for p in sample)
        lines.append(f"- **{label}** ({size} files): {sample_txt}")

    lines.extend(
        [
            "",
            "## Suggested agent workflow",
            "",
            "1. Call `context` with `tier=1` and your task query before broad grep/read.",
            "2. Escalate `tier=2`/`3` only on paths from `suggested_next`.",
            "3. Use `detect_changes` on PR branches; run `update` after merge when `stale_index`.",
            "4. Pair with `.pareto-context-graph/context-map.json` specs (Phase 15).",
            "",
        ]
    )
    return "\n".join(lines)


def write_architecture_report(repo_root: Path, out_path: Path | None = None) -> Path:
    """Write ARCHITECTURE_REPORT.md under .pareto-context-graph/."""
    text = build_architecture_report(repo_root)
    dest = out_path or (repo_root / ".pareto-context-graph" / "ARCHITECTURE_REPORT.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text + "\n")
    return dest
