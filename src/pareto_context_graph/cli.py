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


def _print_init_next_steps(repo: Path, *, from_snapshot: bool, installed: bool) -> None:
    print("\nNext steps:")
    print("  pareto-context-graph doctor")
    if from_snapshot:
        print("  pareto-context-graph sync          # after git pull")
    else:
        print("  pareto-context-graph sync          # keep graph fresh after commits")
    print("  pareto-context-graph serve --watch # MCP with auto-sync")
    if not installed:
        print("  pareto-context-graph install       # configure Cursor/Claude MCP")
    print(f"  Graph: {repo / '.pareto-context-graph/graph.db'}")


def cmd_init(args: argparse.Namespace) -> None:
    """One-shot onboarding: build (+ optional snapshot), install, next steps."""
    cmd_build(args)
    if not args.skip_install:
        cmd_install(
            argparse.Namespace(
                repo=args.repo,
                target=args.target,
                location=args.location,
                force=False,
                print_config=None,
                watch=args.watch,
                yes=True,
            )
        )
    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    _print_init_next_steps(
        repo,
        from_snapshot=bool(args.from_snapshot),
        installed=not args.skip_install,
    )


def cmd_sync(args: argparse.Namespace) -> None:
    """Incremental graph update (+ optional search index catch-up)."""
    from .graph import incremental_update
    from .indexing import SEARCH_INDEX_STATUS_META, count_pending_index_files, ensure_search_indexes

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    profile_name = args.profile or autodetect_profile(repo)
    store = incremental_update(repo)
    print(f"Synced graph for {repo}")
    print(f"  Files:  {store.file_count()}")
    print(f"  Edges:  {store.edge_count()}")

    if args.with_index:
        stats = ensure_search_indexes(store, repo, profile_name=profile_name)
        status = store.get_meta(SEARCH_INDEX_STATUS_META) or "unknown"
        print(f"  Search index: {status} (indexed {stats.get('indexed', 0)})")

    pending = count_pending_index_files(store, repo, profile_name=profile_name)
    if pending:
        print(
            f"  Pending index: {pending} file(s) — "
            "run `pareto-context-graph index` or `sync --with-index`"
        )
    store.close()


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
    search_mode = "eager" if getattr(args, "with_search_index", False) else None

    print(f"Building graph for {repo} ...")
    if profile_name:
        print(f"  Profile: {profile_name}")
    if search_mode == "eager":
        print("  Search index: eager (full symbol/content index during build)")
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
        profile_name=profile_name,
        search_index_mode=search_mode,
    )
    if store.get_meta("build_status") == "noop":
        print("  Graph up to date (skipped rebuild)")
    else:
        index_status = store.get_meta("search_index_status") or "unknown"
        if index_status == "pending":
            print("  Search index: deferred (run `pareto-context-graph index` or query `search`)")
        elif index_status == "complete":
            print("  Search index: complete")
    print(f"  Files:  {store.file_count()}")
    print(f"  Edges:  {store.edge_count()}")
    if since:
        print(f"  Since:  {since}")
    print(f"  Shards: {shards}")
    print(f"  Stored: {repo / '.pareto-context-graph/graph.db'}")
    store.close()


def cmd_index(args: argparse.Namespace) -> None:
    """Build or resume deferred symbol/content search indexes."""
    from .indexing import SEARCH_INDEX_STATUS_META, ensure_search_indexes
    from .store import Store

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    profile_name = args.profile or autodetect_profile(repo)
    store = Store(repo)
    if store.file_count() == 0:
        print("Error: graph not built — run `pareto-context-graph build` first", file=sys.stderr)
        store.close()
        sys.exit(1)

    before = store.get_meta(SEARCH_INDEX_STATUS_META) or "pending"
    stats = ensure_search_indexes(
        store,
        repo,
        profile_name=profile_name,
        force=getattr(args, "force", False),
    )
    after = store.get_meta(SEARCH_INDEX_STATUS_META) or "unknown"
    if args.json:
        print(json.dumps({"before": before, "after": after, **stats}, indent=2))
    else:
        print(f"Search index: {before} → {after}")
        print(f"  Indexed:   {stats.get('indexed', 0)}")
        print(f"  Unchanged: {stats.get('unchanged', 0)}")
        print(f"  Skipped:   {stats.get('skipped', 0)}")
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
    from .repo_registry import build_repo_registry
    from .server import run_server

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    registry = build_repo_registry(repo, getattr(args, "repo_map", None) or [])
    watcher = None
    flusher = FeedbackFlusher(repo, interval=30)
    flusher.start()
    if args.watch:
        debounce_ms = args.debounce_ms
        if debounce_ms is None:
            import os

            debounce_ms = int(os.environ.get("PCG_WATCH_DEBOUNCE_MS", "2000"))
        watcher = GraphWatcher(repo, interval=args.interval, debounce_ms=debounce_ms)
        watcher.start()
    try:
        run_server(registry, transport=args.transport)
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


def cmd_architecture_report(args: argparse.Namespace) -> None:
    from .architecture_report import write_architecture_report

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    out = write_architecture_report(repo)
    print(f"Wrote {out}")


def cmd_detect_changes(args: argparse.Namespace) -> None:
    from .graph_diff import detect_changes

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    payload = detect_changes(repo, base=args.base, max_depth=args.max_depth)
    print(json.dumps(payload, indent=2))


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


def cmd_affected(args: argparse.Namespace) -> None:
    """Suggest tests to run for changed files (reverse structural walk)."""
    from .affected import affected_from_git, compute_affected_tests, read_paths_from_stdin

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    paths = list(args.paths or [])
    if args.stdin:
        paths.extend(read_paths_from_stdin())

    if paths:
        from .store import Store

        store = Store(repo)
        try:
            payload = compute_affected_tests(
                store,
                repo,
                paths,
                max_depth=args.max_depth,
            )
            payload["base"] = args.base
        finally:
            store.close()
    else:
        payload = affected_from_git(repo, base=args.base, max_depth=args.max_depth)

    if args.quiet:
        for test_path in payload.get("tests", []):
            print(test_path)
        return

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    print(f"Changed: {len(payload.get('changed', []))} file(s)")
    print(f"Tests to run: {payload.get('test_count', 0)}")
    for test_path in payload.get("tests", [])[:40]:
        print(f"  - {test_path}")
    if payload.get("test_count", 0) > 40:
        print(f"  ... and {payload['test_count'] - 40} more")


def cmd_install(args: argparse.Namespace) -> None:
    """Auto-configure MCP and steering markers for AI coding tools."""
    from .agent_install import INSTALL_TARGETS, install_agent, print_agent_config

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    target = getattr(args, "target", None) or getattr(args, "platform", None) or "auto"
    if target not in INSTALL_TARGETS:
        print(f"Error: unknown target {target}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "print_config", None):
        payload = print_agent_config(
            repo,
            args.print_config,
            location=getattr(args, "location", "local"),
            watch=getattr(args, "watch", False),
        )
        print(json.dumps(payload, indent=2))
        return

    messages = install_agent(
        repo,
        target,
        location=getattr(args, "location", "local"),
        force=getattr(args, "force", False),
        watch=getattr(args, "watch", False),
    )
    for line in messages:
        print(f"  {line}")
    print("Installed. Restart your editor to activate.")
    print("  Tip: pip install -e '.[tiktoken]' for accurate token budgets (recommended)")


def cmd_uninstall(args: argparse.Namespace) -> None:
    from .agent_install import INSTALL_TARGETS, uninstall_agent

    repo = _resolve_repo_root(Path(args.repo) if args.repo else None)
    target = getattr(args, "target", None) or getattr(args, "platform", None) or "all"
    if target not in INSTALL_TARGETS:
        print(f"Error: unknown target {target}", file=sys.stderr)
        sys.exit(1)
    messages = uninstall_agent(repo, target, location=getattr(args, "location", "local"))
    if not messages:
        print("Nothing to remove.")
        return
    for line in messages:
        print(f"  {line}")


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
    if getattr(args, "agent_ab", False):
        from .agent_ab import (
            DEFAULT_AGENT_AB_BASELINE_PATH,
            check_agent_ab_gate,
            portable_agent_ab_payload,
            run_agent_ab_study,
        )

        agent_ab_baseline_path = Path(
            getattr(args, "agent_ab_baseline", DEFAULT_AGENT_AB_BASELINE_PATH)
        ).resolve()
        ab_result = run_agent_ab_study(repo_overrides=repo_overrides, golden_dir=golden_dir)

        if getattr(args, "update_agent_ab_baseline", False):
            agent_ab_baseline_path.parent.mkdir(parents=True, exist_ok=True)
            agent_ab_baseline_path.write_text(
                json.dumps(portable_agent_ab_payload(ab_result), indent=2) + "\n"
            )
            print(f"Agent A/B baseline written to {agent_ab_baseline_path}")

        if args.json:
            print(json.dumps(ab_result, indent=2))
        else:
            summary = ab_result["summary"]
            pcg = summary["pcg"]
            baseline = summary["baseline"]
            delta = summary["pcg_vs_baseline"]
            print(f"\n{'=' * 60}")
            print("  Agent A/B harness (PCG vs grep+read baseline)")
            print(f"{'=' * 60}\n")
            print(f"  Cases:              {ab_result['cases']}")
            print(
                f"  PCG tool calls:     {pcg['tool_calls']:.1f}  (baseline {baseline['tool_calls']:.1f})"
            )
            print(
                f"  PCG file reads:     {pcg['file_reads']:.1f}  (baseline {baseline['file_reads']:.1f})"
            )
            print(f"  PCG tokens:         {pcg['tokens']:.0f}  (baseline {baseline['tokens']:.0f})")
            print(
                f"  PCG wall time ms:   {pcg['wall_time_ms']:.1f}  (baseline {baseline['wall_time_ms']:.1f})"
            )
            print(
                f"  PCG recall@5:       {pcg['recall_at_5']:.4f}  (baseline {baseline['recall_at_5']:.4f})"
            )
            if delta.get("tool_calls_reduction_pct") is not None:
                print(f"  Tool call reduction:{delta['tool_calls_reduction_pct']:.1f}%")
            if delta.get("tokens_reduction_pct") is not None:
                print(f"  Token reduction:    {delta['tokens_reduction_pct']:.1f}%")
            print(f"  Recall delta:       {delta.get('recall_at_5_delta', 0):+.4f}")

        ab_exit = 0
        if ab_result["cases"] == 0:
            print("Error: no agent A/B cases ran", file=sys.stderr)
            ab_exit = 1

        if getattr(args, "check_agent_ab", False):
            if not agent_ab_baseline_path.exists():
                print(
                    f"Error: agent A/B baseline missing at {agent_ab_baseline_path}",
                    file=sys.stderr,
                )
                sys.exit(1)
            stored = json.loads(agent_ab_baseline_path.read_text())
            gate = check_agent_ab_gate(ab_result, stored)
            if not args.json:
                print(f"\nAgent A/B gate: {'PASS' if gate['passed'] else 'FAIL'}")
                for item in gate.get("failures", []):
                    print(f"  {item}")
            if not gate["passed"]:
                ab_exit = 1

        sys.exit(ab_exit)

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

    if getattr(args, "ablation", False):
        from .eval import format_ablation_table, run_ablation_study

        ablation_result = run_ablation_study(
            repo_overrides=repo_overrides,
            golden_dir=golden_dir,
            compress_stack=getattr(args, "compress_stack", False),
        )
        if args.json:
            print(json.dumps(ablation_result, indent=2))
        else:
            print("\n" + format_ablation_table(ablation_result))
        sys.exit(0)

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
        if summary.get("mean_candidate_pool_recall") is not None:
            print(f"  Pool recall:    {summary['mean_candidate_pool_recall']:.4f}")
            print(f"  Pre-MMR @5:     {summary['mean_pre_mmr_recall_at_5']:.4f}")
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
    p_build.add_argument(
        "--with-search-index",
        action="store_true",
        help="Build symbol/content FTS during cold build (default: lazy on huge profiles)",
    )

    # init (= build + optional install + next steps)
    p_init = sub.add_parser("init", help="Build graph, install MCP, print next steps")
    p_init.add_argument("--commits", type=int, help="Max commits to analyze")
    p_init.add_argument(
        "--since",
        help='Git history window (for example: "12 months ago" or "2025-01-01")',
    )
    p_init.add_argument("--shards", type=int, help="Number of shard workers for build")
    p_init.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Build profile preset")
    p_init.add_argument(
        "--from-snapshot", help="Import snapshot (file or URL) and then incremental update"
    )
    p_init.add_argument(
        "--with-search-index",
        action="store_true",
        help="Build symbol/content FTS during cold build (default: lazy on huge profiles)",
    )
    p_init.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip `install` step (build only)",
    )
    p_init.add_argument(
        "--target",
        choices=sorted(
            {"all", "auto", "cursor", "copilot", "vscode", "claude", "codex", "gemini", "windsurf"}
        ),
        default="auto",
        help="Agent/editor target for install (default: auto)",
    )
    p_init.add_argument(
        "--location",
        choices=["local", "global"],
        default="local",
        help="Write Cursor MCP config locally (.cursor/) or globally (~/.cursor/)",
    )
    p_init.add_argument(
        "--watch",
        action="store_true",
        help="Add --watch to serve args in generated MCP config",
    )

    # sync (= incremental update + optional index catch-up)
    p_sync = sub.add_parser("sync", help="Incremental graph update (+ optional index catch-up)")
    p_sync.add_argument(
        "--profile", choices=sorted(PROFILES.keys()), help="Profile for index catch-up"
    )
    p_sync.add_argument(
        "--with-index",
        action="store_true",
        help="Also run deferred search index catch-up",
    )

    # index (Phase 2 search indexes)
    p_index = sub.add_parser("index", help="Build or resume deferred search indexes")
    p_index.add_argument("--profile", choices=sorted(PROFILES.keys()), help="Build profile preset")
    p_index.add_argument(
        "--force",
        action="store_true",
        help="Clear and rebuild all symbol/content indexes from scratch",
    )
    p_index.add_argument("--json", action="store_true", help="Output stats as JSON")

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
        "--repo-map",
        action="append",
        default=[],
        metavar="KEY=PATH",
        help="Additional named repos for monorepo serve (repeatable)",
    )
    p_serve.add_argument(
        "--transport", default="stdio", choices=["stdio"], help="Transport protocol"
    )
    p_serve.add_argument(
        "--watch", action="store_true", help="Watch repo for edits and sync search index"
    )
    p_serve.add_argument(
        "--interval",
        type=int,
        default=600,
        help="Co-change incremental update interval in seconds (default: 600)",
    )
    p_serve.add_argument(
        "--debounce-ms",
        type=int,
        default=None,
        help="File watcher debounce in ms (default: PCG_WATCH_DEBOUNCE_MS or 2000)",
    )

    # install
    p_install = sub.add_parser("install", help="Auto-configure MCP + steering markers")
    p_install.add_argument("--repo", help="Path to git repository (default: current directory)")
    p_install.add_argument(
        "--force", action="store_true", help="Overwrite existing PCG steering markers"
    )
    p_install.add_argument(
        "--target",
        "--platform",
        dest="target",
        choices=sorted(
            {"all", "auto", "cursor", "copilot", "vscode", "claude", "codex", "gemini", "windsurf"}
        ),
        default="auto",
        help="Agent/editor target (default: auto = cursor+copilot+claude steering)",
    )
    p_install.add_argument(
        "--location",
        choices=["local", "global"],
        default="local",
        help="Write Cursor MCP config locally (.cursor/) or globally (~/.cursor/)",
    )
    p_install.add_argument(
        "--print-config",
        metavar="AGENT",
        help="Print MCP JSON for AGENT (cursor, claude, windsurf, …) without writing files",
    )
    p_install.add_argument(
        "--watch",
        action="store_true",
        help="Add --watch to serve args in generated MCP config",
    )
    p_install.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive install (reserved; install is non-interactive today)",
    )

    p_uninstall = sub.add_parser("uninstall", help="Remove PCG MCP entries and steering markers")
    p_uninstall.add_argument("--repo", help="Path to git repository (default: current directory)")
    p_uninstall.add_argument(
        "--target",
        "--platform",
        dest="target",
        choices=sorted(
            {"all", "auto", "cursor", "copilot", "vscode", "claude", "codex", "gemini", "windsurf"}
        ),
        default="all",
    )
    p_uninstall.add_argument(
        "--location",
        choices=["local", "global"],
        default="local",
    )

    # affected
    p_affected = sub.add_parser(
        "affected",
        help="Suggest tests for changed files (reverse structural/import walk)",
    )
    p_affected.add_argument("--repo", help="Path to git repository (default: current directory)")
    p_affected.add_argument("--base", default="main", help="Git base branch when inferring diff")
    p_affected.add_argument(
        "paths",
        nargs="*",
        help="Changed file paths (default: git diff vs --base)",
    )
    p_affected.add_argument(
        "--stdin",
        action="store_true",
        help="Also read changed paths from stdin (e.g. git diff --name-only | pcg affected --stdin)",
    )
    p_affected.add_argument("--max-depth", type=int, default=3, help="Reverse walk depth limit")
    p_affected.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_affected.add_argument("--quiet", action="store_true", help="Print one test path per line")

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
    p_eval.add_argument(
        "--agent-ab",
        action="store_true",
        help="Headless agent A/B: PCG one-call vs grep+read baseline on golden cases",
    )
    p_eval.add_argument(
        "--agent-ab-baseline",
        default="tests/eval/baseline-agent-ab.json",
        help="Agent A/B regression baseline (default: tests/eval/baseline-agent-ab.json)",
    )
    p_eval.add_argument(
        "--update-agent-ab-baseline",
        action="store_true",
        help="Write agent A/B summary to --agent-ab-baseline",
    )
    p_eval.add_argument(
        "--check-agent-ab",
        action="store_true",
        help="With --agent-ab: fail if PCG recall or tool-call savings regress vs baseline",
    )
    p_eval.add_argument(
        "--ablation",
        action="store_true",
        help="Print per-signal ablation table (recall@5 / pool / pre-MMR deltas)",
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

    p_arch = sub.add_parser(
        "architecture-report",
        help="Write ARCHITECTURE_REPORT.md from graph stats (no LLM)",
    )
    p_arch.add_argument("--repo", default=None, help="Repository path (default: git root)")

    p_dc = sub.add_parser("detect-changes", help="Git diff blast radius + index staleness")
    p_dc.add_argument("--repo", default=None, help="Repository path (default: git root)")
    p_dc.add_argument("--base", default="main", help="Git base branch for diff")
    p_dc.add_argument("--max-depth", type=int, default=2, help="Blast BFS depth")

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
    p_session_clear = session_sub.add_parser(
        "clear", help="Clear .pareto-context-graph/session.json"
    )
    p_session_clear.add_argument("--repo", default=None, help="Repository path (default: git root)")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if getattr(args, "record_build", False):
        args.build = True

    commands = {
        "build": cmd_build,
        "init": cmd_init,
        "sync": cmd_sync,
        "index": cmd_index,
        "query": cmd_query,
        "serve": cmd_serve,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "affected": cmd_affected,
        "metrics": cmd_metrics,
        "eval": cmd_eval,
        "decay-sweep": cmd_decay_sweep,
        "stats": cmd_stats,
        "doctor": cmd_doctor,
        "architecture-report": cmd_architecture_report,
        "detect-changes": cmd_detect_changes,
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
