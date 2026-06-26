"""CLI entrypoint for pareto-context-graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .eval import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_CASES_PATH,
    DEFAULT_COMPRESS_BASELINE_PATH,
    parse_repo_overrides,
    run_evaluation,
)
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
            repo / ".pareto-context-graph" / "snapshot-download.tar.gz",
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
    if store.get_meta("build_status") == "noop":
        print("  Graph up to date (skipped rebuild)")
    print(f"  Files:  {store.file_count()}")
    print(f"  Edges:  {store.edge_count()}")
    if since:
        print(f"  Since:  {since}")
    print(f"  Shards: {shards}")
    print(f"  Stored: {repo / '.pareto-context-graph/graph.db'}")
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
        print("No graph built yet. Run: pareto-context-graph build", file=sys.stderr)
        store.close()
        sys.exit(1)

    changed = get_changed_files(repo, base=args.base)
    if not changed:
        print("No changed files detected.")
        store.close()
        return

    from .features import feature_enabled
    from .savings import build_context_savings
    from .tokenizer import resolve_tokenizer

    results = blast_radius(
        store,
        changed,
        min_weight=min_weight,
        max_depth=max_depth,
        use_structural=feature_enabled("STRUCTURAL_EDGES"),
    )
    existing = filter_existing(repo, [r["path"] for r in results])
    savings = compute_savings(repo, existing)

    tokenizer = resolve_tokenizer(None)
    graph_tokens = savings["blast_tokens"]
    panel = build_context_savings(
        repo,
        graph_tokens=graph_tokens,
        tokenizer=tokenizer.name,
        query=" ".join(changed[:3]),
        seed_files=changed,
    )
    store.close()

    if args.brief:
        print("\nToken savings (brief)")
        print(f"  Corpus (naive):  {panel['naive_corpus_tokens']:,} tokens")
        print(f"  Agent (grep):    {panel['agent_baseline_tokens']:,} tokens")
        print(f"  Graph (blast):   {panel['graph_tokens']:,} tokens")
        print(f"  vs corpus:       {panel['reduction_ratio']:.1f}x")
        print(f"  vs agent:        {panel['reduction_vs_agent']:.1f}x")
        print(f"  tokenizer:       {panel['tokenizer']} ({panel['method']})")
        return

    if args.json:
        output = {
            "changed_files": changed,
            "blast_radius": results,
            "savings": savings,
            "context_savings": panel,
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    print(f"\n{'=' * 60}")
    print("  CODE GRAPH MCP — Blast Radius Report")
    print(f"{'=' * 60}\n")

    print(f"  Changed files:        {len(changed)}")
    print(f"  Blast radius files:   {savings['blast_files']}")
    print(f"  Total repo files:     {savings['full_files']}")

    print(f"\n  {'—' * 40}")
    print(f"  Full repo tokens:     {savings['full_tokens']:,}")
    print(f"  Blast radius tokens:  {savings['blast_tokens']:,}")
    print(f"  Tokens saved:         {savings['saved_tokens']:,}")
    print(f"  Reduction:            {savings['percent_reduction']}%")
    print(f"  Efficiency:           {savings['multiplier']}x fewer tokens")

    print(f"\n  {'—' * 40}")
    print("  Files to review:\n")
    for r in results[:30]:
        marker = "* " if r["depth"] == 0 else "  "
        weight_str = f"(weight: {r['weight']})" if r["depth"] > 0 else "(changed)"
        print(f"    {marker}{r['path']}  {weight_str}")
    if len(results) > 30:
        print(f"    ... and {len(results) - 30} more")
    print()


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    from .daemon import GraphWatcher
    from .feedback import FeedbackFlusher
    from .server import run_server

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    watcher = None
    flusher = FeedbackFlusher(repo, interval=30)
    flusher.start()
    if args.watch:
        watcher = GraphWatcher(repo, interval=args.interval)
        watcher.start()
    try:
        run_server(repo, transport=args.transport)
    finally:
        flusher.stop()
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
    prune_below = args.prune_below if args.prune_below is not None else profile.get("prune_below")

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
    from .doctor import format_doctor_text, gather_doctor_report

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    report = gather_doctor_report(
        repo,
        profile=args.profile,
        commits=args.commits,
        since=args.since,
        shards=args.shards,
    )
    print(format_doctor_text(report))


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


def cmd_bench(args: argparse.Namespace) -> None:
    from .bench import merge_bench_results, run_repo_benchmark

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    repo_key = args.key or repo.name
    entry = run_repo_benchmark(
        repo,
        repo_key,
        build=args.build,
        profile=args.profile,
        commits=args.commits,
        since=args.since,
        shards=args.shards,
        context_rounds=args.rounds,
        skip_incremental=getattr(args, "skip_incremental", False),
    )
    if args.merge_results:
        merge_bench_results(Path(args.merge_results).resolve(), entry)
    print(json.dumps(entry, indent=2))


def cmd_learn(args: argparse.Namespace) -> None:
    from .feedback import FeedbackEventLog, fold_events_to_sqlite
    from .prune_learn import learn_prune_weights, save_prune_weights
    from .ranker import learn_file_weights, lightgbm_available, save_ranker, train_best_ranker
    from .store import DB_DIR, Store

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    fold_stats = fold_events_to_sqlite(repo)
    store = Store(repo)
    rows = store.feedback_rows_by_file()
    store.close()

    weights = learn_file_weights(rows)
    prune_weights = learn_prune_weights(rows)

    out = repo / DB_DIR / "weights.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(weights, indent=2) + "\n")
    print(f"Wrote {len(weights)} learned file weights to {out}")
    prune_path = save_prune_weights(repo, prune_weights)
    print(f"Wrote {len(prune_weights)} learned prune biases to {prune_path}")
    print(
        "Folded events:",
        f"processed={fold_stats['processed']}",
        f"positive={fold_stats['positive']}",
        f"negative={fold_stats['negative']}",
    )

    prefer = getattr(args, "ranker", "auto") or "auto"
    ranker = train_best_ranker(FeedbackEventLog(repo).read_all(), prefer=prefer)
    if ranker:
        ranker_path = save_ranker(repo, ranker)
        model = ranker.to_dict().get("model", "unknown")
        print(f"Wrote {model} ranker to {ranker_path}")
    else:
        hint = ""
        if prefer == "lambdamart" and not lightgbm_available():
            hint = " (install optional extra: pip install -e '.[ranker]')"
        print(f"Skipped ranker training (insufficient labeled samples){hint}")


def cmd_metrics(args: argparse.Namespace) -> None:
    from .metrics import METRICS

    if getattr(args, "serve", False):
        cmd_metrics_serve(args)
        return
    if getattr(args, "prometheus", False):
        print(METRICS.prometheus_text(), end="")
    else:
        print(json.dumps(METRICS.snapshot(), indent=2))


def cmd_metrics_serve(args: argparse.Namespace) -> None:
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from .metrics import METRICS
    from .tracing import recent_spans

    host = getattr(args, "host", "127.0.0.1")
    port = int(getattr(args, "port", 9090))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *log_args: object) -> None:
            return

        def do_GET(self) -> None:
            if self.path.startswith("/metrics"):
                body = METRICS.prometheus_text().encode("utf-8")
                content_type = "text/plain; version=0.0.4"
            elif self.path.startswith("/traces"):
                body = json.dumps({"spans": recent_spans()}, indent=2).encode("utf-8")
                content_type = "application/json"
            elif self.path in ("/", "/health"):
                body = b"ok\n"
                content_type = "text/plain"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer((host, port), Handler)
    print(f"Metrics on http://{host}:{port}/metrics (traces: /traces)", flush=True)
    server.serve_forever()


def cmd_session_clear(args: argparse.Namespace) -> None:
    """Clear persisted session memory (already_have auto-fill)."""
    from .session import clear_session

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    clear_session(repo)
    print(f"Cleared {repo / '.pareto-context-graph' / 'session.json'}")


def cmd_install(args: argparse.Namespace) -> None:
    """Auto-configure for AI coding tools."""
    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    platform = getattr(args, "platform", None) or "all"
    if platform in {"all", "copilot", "vscode"}:
        _install_copilot(repo)
    if platform in {"all", "cursor"}:
        _install_cursor(repo)
    if platform in {"all", "copilot", "cursor", "vscode"}:
        _install_instructions(repo, force=getattr(args, "force", False))
    print("Installed. Restart your editor to activate.")
    print("  Tip: pip install -e '.[tiktoken]' for accurate token budgets (recommended)")


def cmd_eval(args: argparse.Namespace) -> None:
    """Run retrieval-quality evaluation cases against built graphs."""
    from .eval import (
        DEFAULT_BASELINE_PATH,
        DEFAULT_CASES_PATH,
        DEFAULT_COMPRESS_BASELINE_PATH,
        check_compress_stack_gate,
        check_grep_counterfactual_gate,
        compare_compress_baseline,
        compare_to_baseline,
        portable_compress_baseline_payload,
        portable_eval_payload,
    )

    golden_dir = Path(args.golden_dir).resolve() if args.golden_dir else DEFAULT_CASES_PATH
    baseline_path = Path(args.baseline).resolve() if args.baseline else DEFAULT_BASELINE_PATH
    compress_baseline_path = Path(
        getattr(args, "compress_baseline", DEFAULT_COMPRESS_BASELINE_PATH)
    ).resolve()

    if not args.repo_map:
        print("Error: provide --repo-map KEY=/abs/path (repeatable)", file=sys.stderr)
        sys.exit(1)

    repo_overrides = parse_repo_overrides(args.repo_map)
    if getattr(args, "feedback_replay", False):
        from .feedback_replay import (
            MIN_MRR_IMPROVEMENT,
            learning_snapshot,
            per_case_mrr_delta,
            run_feedback_replay_for_repo,
        )

        if len(repo_overrides) != 1:
            print("Error: --feedback-replay requires exactly one --repo-map", file=sys.stderr)
            sys.exit(1)
        repo_key, repo_root = next(iter(repo_overrides.items()))
        min_delta = (
            args.feedback_min_delta if args.feedback_min_delta is not None else MIN_MRR_IMPROVEMENT
        )
        with learning_snapshot(repo_root):
            report = run_feedback_replay_for_repo(
                repo_key,
                repo_root,
                golden_dir=golden_dir,
                min_mrr_improvement=min_delta,
            )
        payload = report.to_dict()
        payload["per_case"] = per_case_mrr_delta(
            report.holdout_results_before,
            report.holdout_results_after,
        )
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"\nFeedback replay ({repo_key})")
            print(f"  Train cases:    {report.train_cases}")
            print(f"  Holdout cases:  {report.holdout_cases}")
            print(f"  Baseline MRR:   {report.baseline_mrr:.4f}")
            print(f"  After learn:    {report.after_mrr:.4f}")
            print(f"  Delta:          {report.mrr_delta:+.4f} (need >= {min_delta:.2f})")
            print(f"  Result:         {'PASS' if report.passed else 'FAIL'}")
            for row in payload["per_case"]:
                print(
                    f"    - {row['case_id']}: {row['mrr_before']:.3f} -> {row['mrr_after']:.3f} "
                    f"({row['delta']:+.3f})"
                )
        sys.exit(0 if report.passed else 1)

    results = run_evaluation(
        repo_overrides=repo_overrides,
        update_golden=args.update_golden,
        golden_dir=golden_dir,
        compress_stack=getattr(args, "compress_stack", False),
    )

    if args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(portable_eval_payload(results), indent=2) + "\n")
        print(f"Baseline written to {baseline_path}")

    if getattr(args, "update_compress_baseline", False):
        if not getattr(args, "compress_stack", False):
            print("Error: --update-compress-baseline requires --compress-stack", file=sys.stderr)
            sys.exit(1)
        compress_baseline_path.parent.mkdir(parents=True, exist_ok=True)
        compress_baseline_path.write_text(
            json.dumps(portable_compress_baseline_payload(results), indent=2) + "\n"
        )
        print(f"Compress baseline written to {compress_baseline_path}")

    if args.json:
        payload = results
        if args.check_baseline and baseline_path.exists():
            baseline = json.loads(baseline_path.read_text())
            payload = {**results, "regression": compare_to_baseline(results, baseline)}
        print(json.dumps(payload, indent=2))
    else:
        summary = results["summary"]
        print(f"\n{'=' * 60}")
        print("  CODE GRAPH MCP — Retrieval Eval")
        print(f"{'=' * 60}\n")
        print(f"  Cases:          {summary['cases']}")
        print(f"  Mean recall@5:  {summary['mean_recall_at_5']:.4f}")
        print(f"  Mean MRR:       {summary['mean_mrr']:.4f}")
        print(f"  Mean NDCG@10:   {summary['mean_ndcg_at_10']:.4f}")
        print(f"  Mean tokens:    {summary['mean_tokens_used']:.2f}")
        bench = summary.get("three_way_benchmark", {})
        print(f"  vs agent (mean):   {summary['mean_reduction_vs_agent']:.2f}x")
        print(f"  vs agent (median): {summary.get('median_reduction_vs_agent', 0):.2f}x")
        print(f"  vs corpus:         {summary['mean_reduction_vs_corpus']:.2f}x")
        if bench:
            print("\n  Three-way token benchmark (mean per case):")
            print(
                f"    corpus: {bench.get('corpus_tokens_mean', 0):,.0f}  "
                f"agent: {bench.get('agent_tokens_mean', 0):,.0f}  "
                f"graph: {bench.get('graph_tokens_mean', 0):,.0f}"
            )
        cs = summary.get("compress_stack") or summary.get("headroom_stack")
        if cs and cs.get("cases"):
            print(f"\n  Compress stack (tier-3 + prune, {cs.get('methods', ['?'])}):")
            print(
                f"    graph tier-3: {cs.get('mean_graph_tokens', 0):,.0f}  "
                f"compressed: {cs.get('mean_compressed_tokens', cs.get('mean_headroom_tokens', 0)):,.0f}  "
                f"extra savings: {cs.get('mean_stack_reduction_vs_graph', 0):.2f}x"
            )
        print()

        for result in results["results"]:
            line = (
                f"- {result['case_id']}: recall@5={result['recall_at_5']:.4f}, "
                f"mrr={result['mrr']:.4f}, tokens={result['tokens_used']}"
            )
            compressed = result.get("compressed_tokens", result.get("headroom_tokens"))
            if compressed is not None:
                line += (
                    f", tier3→compressed={result.get('graph_tokens_tier3')}→"
                    f"{compressed} ({result.get('stack_reduction_vs_graph', 0):.1f}x)"
                )
            print(line)

    exit_code = 0
    if results["summary"]["cases"] == 0:
        print("Error: no cases ran", file=sys.stderr)
        sys.exit(1)

    if any(r["returned_count"] == 0 for r in results["results"]):
        print("Warning: some cases returned no files", file=sys.stderr)
        exit_code = 1

    if getattr(args, "compress_stack", False):
        gate = check_compress_stack_gate(results)
        if not args.json:
            print(f"\nCompress gate: {'PASS' if gate['passed'] else 'FAIL'}")
            for item in gate.get("failures", []):
                print(f"  {item}")
        if not gate["passed"]:
            exit_code = 1

    if args.check_baseline:
        if not baseline_path.exists():
            print(f"Error: baseline missing at {baseline_path}", file=sys.stderr)
            sys.exit(1)
        baseline = json.loads(baseline_path.read_text())
        report = compare_to_baseline(results, baseline)
        if not args.json:
            print(f"\nRegression check: {'PASS' if report['passed'] else 'FAIL'}")
            for item in report["regressions"]:
                print(
                    f"  {item['metric']}: {item['baseline']} -> {item['current']} ({item['delta']})"
                )
        if not report["passed"]:
            exit_code = 1

        grep_gate = check_grep_counterfactual_gate(results["results"])
        if not args.json:
            print(
                f"\nGrep counterfactual gate: {'PASS' if grep_gate['passed'] else 'FAIL'} "
                f"({grep_gate['graph_not_losing_cases']}/{grep_gate['compared_cases']} comparable)"
            )
            for item in grep_gate.get("failures", []):
                print(
                    f"  {item['case_id']}: grep recall {item['agent_recall_at_5']:.4f} "
                    f"> graph {item['graph_recall_at_5']:.4f}, "
                    f"tokens {item['graph_tokens']} > {item['agent_baseline_tokens']}"
                )
        if not grep_gate["passed"]:
            exit_code = 1

    if getattr(args, "check_compress_baseline", False):
        if not getattr(args, "compress_stack", False):
            print("Error: --check-compress-baseline requires --compress-stack", file=sys.stderr)
            sys.exit(1)
        if not compress_baseline_path.exists():
            print(f"Error: compress baseline missing at {compress_baseline_path}", file=sys.stderr)
            sys.exit(1)
        compress_baseline = json.loads(compress_baseline_path.read_text())
        compress_report = compare_compress_baseline(results, compress_baseline)
        if not args.json:
            print(f"\nCompress regression: {'PASS' if compress_report['passed'] else 'FAIL'}")
            for item in compress_report.get("failures", []):
                print(f"  {item}")
        if not compress_report["passed"]:
            sys.exit(1)

    sys.exit(exit_code)


def _install_copilot(repo: Path) -> None:
    """Write MCP config for GitHub Copilot (VS Code)."""
    mcp_dir = repo / ".vscode"
    mcp_dir.mkdir(exist_ok=True)
    settings_path = mcp_dir / "mcp.json"

    config = {
        "servers": {
            "pareto-context-graph": {
                "command": "pareto-context-graph",
                "args": ["serve", "--repo", str(repo)],
                "type": "stdio",
            }
        }
    }

    if settings_path.exists():
        existing = json.loads(settings_path.read_text())
        servers = existing.get("servers", {})
        servers["pareto-context-graph"] = config["servers"]["pareto-context-graph"]
        existing["servers"] = servers
        settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    else:
        settings_path.write_text(json.dumps(config, indent=2) + "\n")

    print(f"  Copilot MCP config written to {settings_path}")


def _install_cursor(repo: Path) -> None:
    """Write MCP config for Cursor (project-local)."""
    cursor_dir = repo / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_path = cursor_dir / "mcp.json"
    entry = {
        "command": "pareto-context-graph",
        "args": ["serve", "--repo", str(repo.resolve())],
    }
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            existing = {}
        servers = existing.get("mcpServers", {})
        servers["pareto-context-graph"] = entry
        existing["mcpServers"] = servers
        mcp_path.write_text(json.dumps(existing, indent=2) + "\n")
    else:
        mcp_path.write_text(json.dumps({"mcpServers": {"pareto-context-graph": entry}}, indent=2) + "\n")
    print(f"  Cursor MCP config written to {mcp_path}")


def _install_instructions(repo: Path, force: bool = False) -> None:
    """Write AI instructions that enforce get_context usage on every prompt."""
    instructions_dir = repo / ".github"
    instructions_dir.mkdir(exist_ok=True)
    instructions_path = instructions_dir / "copilot-instructions.md"

    instructions = """\
# Code Graph MCP — Context Instructions

## IMPORTANT: Always scope your context before acting

Before answering any question or performing any task in this repository,
call the `pareto_context_graph` MCP tool to get relevant context.

### How to use:

1. Identify which file(s) the user is asking about or working in.
2. Call `pareto_context_graph` with command="context" and those file paths:
   ```json
   {"command": "context", "files": ["path/to/file.rb"], "query": "user's question", "tier": 1}
   ```
3. Tier 1 (default) gives summaries. If you need more detail on specific files,
   call again with tier=2 (signatures) or tier=3 (code chunks).
4. On follow-up prompts, pass `already_have` with files you already read:
   ```json
   {"command": "context", "files": [...], "already_have": ["file1.rb", "file2.rb"]}
   ```
5. Start a **new task** with a fresh session — call `session_clear` or
   `pareto-context-graph session clear` so stale paths are not auto-merged:
   ```json
   {"command": "session_clear"}
   ```
6. Do NOT scan, grep, or read other files unless the tool's results are insufficient.

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
For a **new user task**, call `session_clear` first (or CLI: `pareto-context-graph session clear`).

### Token budgets:
Install the tiktoken extra for honest budgets: `pip install -e '.[tiktoken]'`.
Use `diagnostics: true` on context for per-candidate score breakdown.
Every `context` response includes `suggested_next` (tier escalation / compression hints).

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
        prog="pareto-context-graph",
        description="Pareto-ranked, token-budgeted context for AI coding assistants",
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
    p_build.add_argument(
        "--from-snapshot", help="Import snapshot (file or URL) and then incremental update"
    )

    # query
    p_query = sub.add_parser("query", help="Show blast radius and token savings")
    p_query.add_argument("--base", default="main", help="Base branch (default: main)")
    p_query.add_argument("--min-weight", type=int, help="Min co-change count")
    p_query.add_argument("--depth", type=int, help="Max graph depth")
    p_query.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Query profile preset")
    p_query.add_argument("--json", action="store_true", help="Output as JSON")
    p_query.add_argument(
        "--brief",
        action="store_true",
        help="Show compact token savings panel (corpus vs agent vs graph)",
    )

    # serve
    p_serve = sub.add_parser("serve", help="Start MCP server")
    p_serve.add_argument("--repo", help="Path to git repository (default: current directory)")
    p_serve.add_argument(
        "--transport", default="stdio", choices=["stdio"], help="Transport protocol"
    )
    p_serve.add_argument(
        "--watch", action="store_true", help="Run periodic incremental updates while serving"
    )
    p_serve.add_argument(
        "--interval", type=int, default=600, help="Watch update interval in seconds"
    )

    # install
    p_install = sub.add_parser("install", help="Auto-configure for AI coding tools")
    p_install.add_argument(
        "--force", action="store_true", help="Overwrite existing Code Graph instructions"
    )
    p_install.add_argument(
        "--platform",
        choices=["all", "cursor", "copilot", "vscode"],
        default="all",
        help="Editor target (default: all)",
    )

    # metrics
    p_metrics = sub.add_parser("metrics", help="Show or serve in-process Prometheus-style metrics")
    p_metrics.add_argument("--prometheus", action="store_true", help="Emit Prometheus text format")
    p_metrics.add_argument(
        "--serve", action="store_true", help="HTTP server on /metrics and /traces"
    )
    p_metrics.add_argument("--host", default="127.0.0.1", help="Bind host when --serve")
    p_metrics.add_argument("--port", type=int, default=9090, help="Bind port when --serve")

    # eval
    p_eval = sub.add_parser("eval", help="Run retrieval-quality evaluation cases")
    p_eval.add_argument(
        "--golden-dir",
        default=str(DEFAULT_CASES_PATH),
        help="Directory containing golden/<repo>/cases.json (default: tests/eval/golden)",
    )
    p_eval.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE_PATH),
        help="Baseline JSON for regression checks (default: tests/eval/baseline.json)",
    )
    p_eval.add_argument(
        "--repo-map",
        action="append",
        default=[],
        metavar="KEY=/abs/path",
        help="Map a repo key from the eval cases to an absolute repo path; may be passed multiple times",
    )
    p_eval.add_argument(
        "--update-golden", action="store_true", help="Write per-case snapshots under golden/<repo>/"
    )
    p_eval.add_argument(
        "--update-baseline", action="store_true", help="Write results to baseline.json"
    )
    p_eval.add_argument(
        "--check-baseline", action="store_true", help="Exit non-zero if metrics regress vs baseline"
    )
    p_eval.add_argument("--json", action="store_true", help="Output eval results as JSON")
    p_eval.add_argument(
        "--compress-stack",
        "--headroom-stack",
        dest="compress_stack",
        action="store_true",
        help="Also run tier-3 context and report graph → prune compression token savings",
    )
    p_eval.add_argument(
        "--compress-baseline",
        default=str(DEFAULT_COMPRESS_BASELINE_PATH),
        help="Compression regression baseline (default: tests/eval/baseline-compress.json)",
    )
    p_eval.add_argument(
        "--update-compress-baseline",
        action="store_true",
        help="Write compress_stack summary to --compress-baseline",
    )
    p_eval.add_argument(
        "--check-compress-baseline",
        action="store_true",
        help="With --compress-stack: fail if compression regresses vs compress baseline",
    )
    p_eval.add_argument(
        "--feedback-replay",
        action="store_true",
        help="Run held-out MRR check after synthetic feedback replay + learn",
    )
    p_eval.add_argument(
        "--feedback-min-delta",
        type=float,
        default=None,
        help="Minimum held-out MRR gain required (default: 0.03)",
    )

    # decay-sweep
    p_decay = sub.add_parser("decay-sweep", help="Apply decay and optional prune to edge weights")
    p_decay.add_argument("--half-life-days", type=float, help="Decay half-life in days")
    p_decay.add_argument(
        "--prune-below", type=float, help="Delete edges with weight below threshold"
    )
    p_decay.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Profile preset")

    # stats
    sub.add_parser("stats", help="Output graph stats as JSON")

    # doctor
    p_doctor = sub.add_parser("doctor", help="Print graph health diagnostics")
    p_doctor.add_argument(
        "--profile", choices=sorted(PROFILES.keys()), help="Build profile for estimate"
    )
    p_doctor.add_argument("--commits", type=int, help="Max commits cap for estimate")
    p_doctor.add_argument(
        "--since", help="Git since expression for estimate (e.g. '12 months ago')"
    )
    p_doctor.add_argument("--shards", type=int, help="Shard count for estimate")

    # snapshot export/import
    p_snap = sub.add_parser("snapshot", help="Export/import graph snapshot")
    snap_sub = p_snap.add_subparsers(dest="snapshot_command")
    p_snap_export = snap_sub.add_parser("export", help="Export snapshot tar.gz")
    p_snap_export.add_argument("out", help="Output .tar.gz path")
    p_snap_import = snap_sub.add_parser("import", help="Import snapshot tar.gz")
    p_snap_import.add_argument("source", help="Input .tar.gz path")

    # embed
    sub.add_parser("embed", help="Build optional embeddings sidecar")

    # bench (Phase 6 stress metrics)
    p_bench = sub.add_parser("bench", help="Record huge-repo stress metrics for a built graph")
    p_bench.add_argument("--key", help="Repo key for results JSON (default: directory name)")
    p_bench.add_argument(
        "--profile", choices=sorted(PROFILES.keys()), help="Context profile preset"
    )
    p_bench.add_argument("--commits", type=int, help="Max commits when --build is set")
    p_bench.add_argument("--since", help="Git --since window when --build is set")
    p_bench.add_argument("--shards", type=int, help="Shard workers when --build is set")
    p_bench.add_argument("--rounds", type=int, default=3, help="Context latency rounds per query")
    p_bench.add_argument("--build", action="store_true", help="Build graph before measuring")
    p_bench.add_argument(
        "--record-build",
        action="store_true",
        help="Alias for --build (used by scripts/bench_huge.sh)",
    )
    p_bench.add_argument(
        "--merge-results",
        metavar="PATH",
        help="Merge entry into bench results JSON (default: skip)",
    )
    p_bench.add_argument(
        "--skip-incremental",
        action="store_true",
        help="Skip incremental update measurement (faster; use in CI)",
    )

    # learn
    p_learn = sub.add_parser("learn", help="Learn ranking weights from feedback")
    p_learn.add_argument(
        "--ranker",
        choices=["auto", "logistic", "lambdamart"],
        default="auto",
        help="Ranker to train (default: auto = LambdaMART if lightgbm installed)",
    )

    p_session = sub.add_parser("session", help="Session memory helpers")
    session_sub = p_session.add_subparsers(dest="session_command")
    p_session_clear = session_sub.add_parser("clear", help="Clear .pareto-context-graph/session.json")
    p_session_clear.add_argument("--repo", default=None, help="Repository path (default: git root)")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if getattr(args, "record_build", False):
        args.build = True

    commands = {
        "build": cmd_build,
        "query": cmd_query,
        "serve": cmd_serve,
        "install": cmd_install,
        "metrics": cmd_metrics,
        "eval": cmd_eval,
        "decay-sweep": cmd_decay_sweep,
        "stats": cmd_stats,
        "doctor": cmd_doctor,
        "embed": cmd_embed,
        "bench": cmd_bench,
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

    if args.command == "session":
        if args.session_command == "clear":
            cmd_session_clear(args)
            return
        p_session.print_help()
        sys.exit(1)

    commands[args.command](args)


if __name__ == "__main__":
    main()
