"""CLI entrypoint for code-graph-mcp."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from . import __version__
from .eval import DEFAULT_CASES_PATH, run_evaluation, parse_repo_overrides
from .profiles import PROFILES, autodetect_profile, resolve_profile


def _resolve_repo_root(path: Path | None = None) -> Path:
    """Find the git repository root."""
    import subprocess

    cwd = path or Path.cwd()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Error: not a git repository", file=sys.stderr)
        sys.exit(1)
    return Path(result.stdout.strip())


def cmd_build(args: argparse.Namespace) -> None:
    """Build the co-change graph from git history."""
    from .graph import build_graph_sharded, incremental_update
    from .snapshot import fetch_snapshot, import_snapshot

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    profile_name = args.profile or autodetect_profile(repo)
    profile = resolve_profile(profile_name)

    commits = args.commits if args.commits is not None else profile.get("commits", 5000)
    since = args.since if args.since is not None else profile.get("since")
    shards = args.shards if args.shards is not None else profile.get("shards", 1)

    print(f"Building graph for {repo} ...")
    if profile_name:
        print(f"  Profile: {profile_name}")
    if args.from_snapshot:
        snapshot_path = fetch_snapshot(
            args.from_snapshot,
            repo / ".code-graph" / "snapshot-download.tar.gz",
        )
        import_snapshot(repo, snapshot_path)
        store = incremental_update(repo)
        print(f"  Bootstrapped from snapshot: {args.from_snapshot}")
        print(f"  Files:  {store.file_count()}")
        print(f"  Edges:  {store.edge_count()}")
        store.close()
        return

    store = build_graph_sharded(
        repo,
        max_commits=commits,
        since=since,
        shards=shards,
    )
    print(f"  Files:  {store.file_count()}")
    print(f"  Edges:  {store.edge_count()}")
    if since:
        print(f"  Since:  {since}")
    print(f"  Shards: {shards}")
    print(f"  Stored: {repo / '.code-graph/graph.db'}")
    store.close()


def cmd_query(args: argparse.Namespace) -> None:
    """Show blast radius and token savings for current changes."""
    from .blast import blast_radius, filter_existing
    from .graph import get_changed_files
    from .store import Store
    from .tokens import compute_savings

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    profile_name = args.profile or autodetect_profile(repo)
    profile = resolve_profile(profile_name)
    min_weight = args.min_weight if args.min_weight is not None else profile.get("min_weight", 2)
    max_depth = args.depth if args.depth is not None else profile.get("max_depth", 2)

    store = Store(repo)

    if store.file_count() == 0:
        print("No graph built yet. Run: code-graph-mcp build", file=sys.stderr)
        store.close()
        sys.exit(1)

    changed = get_changed_files(repo, base=args.base)
    if not changed:
        print("No changed files detected.")
        store.close()
        return

    results = blast_radius(
        store, changed, min_weight=min_weight, max_depth=max_depth
    )
    existing = filter_existing(repo, [r["path"] for r in results])
    savings = compute_savings(repo, existing)
    store.close()

    if args.json:
        output = {
            "changed_files": changed,
            "blast_radius": results,
            "savings": savings,
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    print(f"\n{'='*60}")
    print(f"  CODE GRAPH MCP — Blast Radius Report")
    print(f"{'='*60}\n")

    print(f"  Changed files:        {len(changed)}")
    print(f"  Blast radius files:   {savings['blast_files']}")
    print(f"  Total repo files:     {savings['full_files']}")

    print(f"\n  {'—'*40}")
    print(f"  Full repo tokens:     {savings['full_tokens']:,}")
    print(f"  Blast radius tokens:  {savings['blast_tokens']:,}")
    print(f"  Tokens saved:         {savings['saved_tokens']:,}")
    print(f"  Reduction:            {savings['percent_reduction']}%")
    print(f"  Efficiency:           {savings['multiplier']}x fewer tokens")

    print(f"\n  {'—'*40}")
    print(f"  Files to review:\n")
    for r in results[:30]:
        marker = "* " if r["depth"] == 0 else "  "
        weight_str = f"(weight: {r['weight']})" if r["depth"] > 0 else "(changed)"
        print(f"    {marker}{r['path']}  {weight_str}")
    if len(results) > 30:
        print(f"    ... and {len(results) - 30} more")
    print()


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    from .server import run_server
    from .daemon import GraphWatcher

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    watcher = None
    if args.watch:
        watcher = GraphWatcher(repo, interval=args.interval)
        watcher.start()
    try:
        run_server(repo, transport=args.transport)
    finally:
        if watcher is not None:
            watcher.stop()


def cmd_decay_sweep(args: argparse.Namespace) -> None:
    """Apply time-decay to edges and optionally prune weak links."""
    from .graph import decay_sweep

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    profile_name = args.profile or autodetect_profile(repo)
    profile = resolve_profile(profile_name)
    half_life_days = (
        args.half_life_days
        if args.half_life_days is not None
        else profile.get("half_life_days", 180)
    )
    prune_below = (
        args.prune_below
        if args.prune_below is not None
        else profile.get("prune_below")
    )

    print(f"Applying decay sweep for {repo} ...")
    if profile_name:
        print(f"  Profile: {profile_name}")
    store = decay_sweep(
        repo,
        half_life_days=float(half_life_days),
        prune_below=prune_below,
    )
    print(f"  Files:  {store.file_count()}")
    print(f"  Edges:  {store.edge_count()}")
    print(f"  Half-life days: {half_life_days}")
    if prune_below is not None:
        print(f"  Pruned below:   {prune_below}")
    store.close()


def cmd_stats(args: argparse.Namespace) -> None:
    """Print graph stats as JSON."""
    from .store import Store

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    store = Store(repo)
    payload = store.graph_stats()
    payload.update(
        {
            "repo": str(repo),
            "last_build_commits": store.get_meta("last_build_commits"),
            "total_commits_scanned": store.get_meta("total_commits_scanned"),
            "build_strategy": store.get_meta("build_strategy"),
            "last_build_since": store.get_meta("last_build_since"),
        }
    )
    store.close()
    print(json.dumps(payload, indent=2))


def cmd_doctor(args: argparse.Namespace) -> None:
    """Print a human-readable graph health report."""
    from .store import Store
    import subprocess

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    store = Store(repo)
    stats = store.graph_stats()
    last_hash = store.get_meta("last_commit_hash")
    build_strategy = store.get_meta("build_strategy")
    last_since = store.get_meta("last_build_since")
    store.close()

    age_seconds = None
    if last_hash:
        result = subprocess.run(
            ["git", "show", "-s", "--format=%ct", last_hash],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                age_seconds = max(0, int(time.time()) - int(result.stdout.strip()))
            except ValueError:
                age_seconds = None

    print(f"Graph Doctor for {repo}")
    print("-" * 60)
    print(f"Files:           {stats['files']}")
    print(f"Edges:           {stats['edges']}")
    print(f"P95 degree:      {stats['p95_degree']}")
    print(f"Build strategy:  {build_strategy or 'unknown'}")
    print(f"Build since:     {last_since or '(none)'}")
    if age_seconds is None:
        print("Graph age:       unknown")
    else:
        print(f"Graph age:       {age_seconds // 3600}h")
    print("Top hubs:")
    for hub in stats["top_hubs"]:
        print(f"  - {hub['path']}: {hub['degree']}")


def cmd_snapshot_export(args: argparse.Namespace) -> None:
    from .snapshot import export_snapshot

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    out = Path(args.out).expanduser().resolve()
    export_snapshot(repo, out)
    print(f"Snapshot exported to {out}")


def cmd_snapshot_import(args: argparse.Namespace) -> None:
    from .snapshot import import_snapshot

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    src = Path(args.source).expanduser().resolve()
    import_snapshot(repo, src)
    print(f"Snapshot imported from {src}")


def cmd_embed(args: argparse.Namespace) -> None:
    from .embed import build_embeddings

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    result = build_embeddings(repo)
    print(json.dumps(result, indent=2))


def cmd_learn(args: argparse.Namespace) -> None:
    from .store import DB_DIR, Store

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    store = Store(repo)
    rows = store.conn.execute(
        "SELECT file_path, SUM(used), COUNT(*) FROM feedback GROUP BY file_path"
    ).fetchall()
    store.close()

    weights = {}
    for file_path, used_count, total_count in rows:
        total = max(1, int(total_count))
        ratio = float(used_count) / total
        # logit-like score with guard rails for 0/1 values
        ratio = min(0.99, max(0.01, ratio))
        weights[file_path] = math.log(ratio / (1 - ratio))

    out = repo / DB_DIR / "weights.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(weights, indent=2) + "\n")
    print(f"Wrote {len(weights)} learned file weights to {out}")


def cmd_install(args: argparse.Namespace) -> None:
    """Auto-configure for AI coding tools."""
    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    _install_copilot(repo)
    _install_instructions(repo, force=getattr(args, "force", False))
    print("Installed. Restart your editor to activate.")


def cmd_eval(args: argparse.Namespace) -> None:
    """Run retrieval-quality evaluation cases against built graphs."""
    cases_path = Path(args.cases).resolve() if args.cases else _resolve_repo_root() / DEFAULT_CASES_PATH
    repo_overrides = parse_repo_overrides(args.repo_map or [])
    results = run_evaluation(
        cases_path=cases_path,
        repo_overrides=repo_overrides,
        update_golden=args.update_golden,
    )

    if args.json:
        print(json.dumps(results, indent=2))
        return

    summary = results["summary"]
    print(f"\n{'='*60}")
    print("  CODE GRAPH MCP — Retrieval Eval")
    print(f"{'='*60}\n")
    print(f"  Cases:          {summary['cases']}")
    print(f"  Mean recall@5:  {summary['mean_recall_at_5']:.4f}")
    print(f"  Mean MRR:       {summary['mean_mrr']:.4f}")
    print(f"  Mean NDCG@10:   {summary['mean_ndcg_at_10']:.4f}")
    print(f"  Mean tokens:    {summary['mean_tokens_used']:.2f}\n")

    for result in results["results"]:
        print(f"- {result['case_id']}: recall@5={result['recall_at_5']:.4f}, mrr={result['mrr']:.4f}, ndcg@10={result['ndcg_at_10']:.4f}, tokens={result['tokens_used']}")


def _install_copilot(repo: Path) -> None:
    """Write MCP config for GitHub Copilot (VS Code)."""
    mcp_dir = repo / ".vscode"
    mcp_dir.mkdir(exist_ok=True)
    settings_path = mcp_dir / "mcp.json"

    config = {
        "servers": {
            "code-graph-mcp": {
                "command": "code-graph-mcp",
                "args": ["serve", "--repo", str(repo)],
                "type": "stdio",
            }
        }
    }

    if settings_path.exists():
        existing = json.loads(settings_path.read_text())
        servers = existing.get("servers", {})
        servers["code-graph-mcp"] = config["servers"]["code-graph-mcp"]
        existing["servers"] = servers
        settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    else:
        settings_path.write_text(json.dumps(config, indent=2) + "\n")

    print(f"  Copilot MCP config written to {settings_path}")


def _install_instructions(repo: Path, force: bool = False) -> None:
    """Write AI instructions that enforce get_context usage on every prompt."""
    instructions_dir = repo / ".github"
    instructions_dir.mkdir(exist_ok=True)
    instructions_path = instructions_dir / "copilot-instructions.md"

    instructions = """\
# Code Graph MCP — Context Instructions

## IMPORTANT: Always scope your context before acting

Before answering any question or performing any task in this repository,
call the `code_graph` MCP tool to get relevant context.

### How to use:

1. Identify which file(s) the user is asking about or working in.
2. Call `code_graph` with command="context" and those file paths:
   ```json
   {"command": "context", "files": ["path/to/file.rb"], "query": "user's question", "tier": 1}
   ```
3. Tier 1 (default) gives summaries. If you need more detail on specific files,
   call again with tier=2 (signatures) or tier=3 (code chunks).
4. On follow-up prompts, pass `already_have` with files you already read:
   ```json
   {"command": "context", "files": [...], "already_have": ["file1.rb", "file2.rb"]}
   ```
5. Do NOT scan, grep, or read other files unless the tool's results are insufficient.

### Why:
This repository has a co-change graph built from git history plus import/keyword analysis.
The tool identifies the files most likely to matter — ones that provably changed together,
share imports, or follow naming conventions — so you read the right files, not random ones.
Less noise = fewer hallucinations = more accurate answers.

### Tiers:
- tier=1: File paths + 1-line summaries (cheapest, use for orientation — ~30 tokens/file)
- tier=2: Function/class signatures (use when you need API shape)
- tier=3: Relevant code chunks (use when you need implementation details)

### Delta context (multi-turn):
On follow-up prompts, always pass `already_have` with files already in the conversation.
This skips redundant re-reads and keeps each turn focused on new context only.

### Other commands:
- `search` — Find files by name/path (e.g. `{"command": "search", "query": "patient"}`)
- `neighbours` — Co-change neighbours for a file (e.g. `{"command": "neighbours", "path": "app/models/patient.rb"}`)
- `blast` — Files affected by current git diff
- `stats` — File/edge counts for the graph
- `hotspots` — Most co-changed files (top N)
- `communities` — Detected file clusters (architectural modules)
"""

    if instructions_path.exists():
        existing = instructions_path.read_text()
        if "Code Graph MCP" not in existing:
            # File exists but doesn't have our section — append
            instructions_path.write_text(existing + "\n" + instructions)
            print(f"  Instructions appended to {instructions_path}")
        elif force:
            # Replace our section in-place (everything from the header onward)
            marker = "# Code Graph MCP"
            idx = existing.index(marker)
            instructions_path.write_text(existing[:idx] + instructions)
            print(f"  Instructions updated in {instructions_path}")
        else:
            print(f"  Instructions already present in {instructions_path} (use --force to update)")
    else:
        instructions_path.write_text(instructions)
        print(f"  Instructions written to {instructions_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="code-graph-mcp",
        description="Git-based blast-radius analysis for token-efficient AI code reviews",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--repo", help="Path to git repository (default: current directory)")

    sub = parser.add_subparsers(dest="command")

    # build
    p_build = sub.add_parser("build", help="Build the co-change graph from git history")
    p_build.add_argument("--commits", type=int, help="Max commits to analyze")
    p_build.add_argument(
        "--since",
        help='Git history window (for example: "12 months ago" or "2025-01-01")',
    )
    p_build.add_argument("--shards", type=int, help="Number of shard workers for build")
    p_build.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Build profile preset")
    p_build.add_argument("--from-snapshot", help="Import snapshot (file or URL) and then incremental update")

    # query
    p_query = sub.add_parser("query", help="Show blast radius and token savings")
    p_query.add_argument("--base", default="main", help="Base branch (default: main)")
    p_query.add_argument("--min-weight", type=int, help="Min co-change count")
    p_query.add_argument("--depth", type=int, help="Max graph depth")
    p_query.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Query profile preset")
    p_query.add_argument("--json", action="store_true", help="Output as JSON")

    # serve
    p_serve = sub.add_parser("serve", help="Start MCP server")
    p_serve.add_argument("--repo", help="Path to git repository (default: current directory)")
    p_serve.add_argument("--transport", default="stdio", choices=["stdio"], help="Transport protocol")
    p_serve.add_argument("--watch", action="store_true", help="Run periodic incremental updates while serving")
    p_serve.add_argument("--interval", type=int, default=600, help="Watch update interval in seconds")

    # install
    p_install = sub.add_parser("install", help="Auto-configure for AI coding tools")
    p_install.add_argument("--force", action="store_true", help="Overwrite existing Code Graph instructions")

    # eval
    p_eval = sub.add_parser("eval", help="Run retrieval-quality evaluation cases")
    p_eval.add_argument("--cases", help="Path to eval cases JSON (default: tests/eval/cases.json)")
    p_eval.add_argument(
        "--repo-map",
        action="append",
        default=[],
        metavar="KEY=/abs/path",
        help="Map a repo key from the eval cases to an absolute repo path; may be passed multiple times",
    )
    p_eval.add_argument("--update-golden", action="store_true", help="Write per-case golden snapshots to tests/eval/golden")
    p_eval.add_argument("--json", action="store_true", help="Output eval results as JSON")

    # decay-sweep
    p_decay = sub.add_parser("decay-sweep", help="Apply decay and optional prune to edge weights")
    p_decay.add_argument("--half-life-days", type=float, help="Decay half-life in days")
    p_decay.add_argument("--prune-below", type=float, help="Delete edges with weight below threshold")
    p_decay.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Profile preset")

    # stats
    sub.add_parser("stats", help="Output graph stats as JSON")

    # doctor
    sub.add_parser("doctor", help="Print graph health diagnostics")

    # snapshot export/import
    p_snap = sub.add_parser("snapshot", help="Export/import graph snapshot")
    snap_sub = p_snap.add_subparsers(dest="snapshot_command")
    p_snap_export = snap_sub.add_parser("export", help="Export snapshot tar.gz")
    p_snap_export.add_argument("out", help="Output .tar.gz path")
    p_snap_import = snap_sub.add_parser("import", help="Import snapshot tar.gz")
    p_snap_import.add_argument("source", help="Input .tar.gz path")

    # embed
    sub.add_parser("embed", help="Build optional embeddings sidecar")

    # learn
    sub.add_parser("learn", help="Learn ranking weights from feedback")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "build": cmd_build,
        "query": cmd_query,
        "serve": cmd_serve,
        "install": cmd_install,
        "eval": cmd_eval,
        "decay-sweep": cmd_decay_sweep,
        "stats": cmd_stats,
        "doctor": cmd_doctor,
        "embed": cmd_embed,
        "learn": cmd_learn,
    }
    if args.command == "snapshot":
        if args.snapshot_command == "export":
            cmd_snapshot_export(args)
            return
        if args.snapshot_command == "import":
            cmd_snapshot_import(args)
            return
        p_snap.print_help()
        sys.exit(1)

    commands[args.command](args)


if __name__ == "__main__":
    main()
