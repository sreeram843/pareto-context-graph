"""Pre-build wall-time and disk estimates for `doctor` (Phase 10.4)."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .build_profile import read_build_profile
from .profiles import PROFILES, autodetect_profile, resolve_profile
from .symbols import CODE_EXTENSIONS

# Measured OSS anchors (docs/BENCHMARKS.md, 2026-06-24).
_ANCHORS: tuple[tuple[int, int, float, float], ...] = (
    # (commits_in_window, tracked_source_files, build_sec, db_mb)
    (5_000, 3_500, 17.0, 24.0),  # fastapi tiny profile (search-heavy)
    (5_150, 10_500, 792.0, 289.0),  # kubernetes huge
    (100_000, 40_000, 37_877.0, 1_200.0),  # linux huge
)


@dataclass(frozen=True)
class BuildPlan:
    profile: str | None
    commits_cap: int
    since: str | None
    shards: int


@dataclass(frozen=True)
class BuildInputs:
    plan: BuildPlan
    commits_in_window: int
    tracked_source_files: int
    total_commits: int | None


def resolve_build_plan(
    repo_root: Path,
    *,
    profile: str | None = None,
    commits: int | None = None,
    since: str | None = None,
    shards: int | None = None,
) -> BuildPlan:
    profile_name = profile or autodetect_profile(repo_root)
    preset = resolve_profile(profile_name) if profile_name else {}
    return BuildPlan(
        profile=profile_name,
        commits_cap=commits if commits is not None else int(preset.get("commits", 5_000)),
        since=since if since is not None else preset.get("since"),
        shards=shards if shards is not None else int(preset.get("shards", 1)),
    )


def _run_git(args: list[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def count_commits_in_window(
    repo_root: Path,
    *,
    max_commits: int,
    since: str | None,
) -> int:
    args = ["log", "--oneline", "--no-merges", f"-{max_commits}"]
    if since:
        args.append(f"--since={since}")
    result = _run_git(args, repo_root)
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def count_tracked_source_files(repo_root: Path) -> int:
    result = _run_git(["ls-files"], repo_root)
    if result.returncode != 0:
        return 0
    count = 0
    for path in result.stdout.splitlines():
        if not path or path.startswith(".pareto-context-graph/"):
            continue
        if Path(path).suffix.lower() in CODE_EXTENSIONS:
            count += 1
    return count


def count_total_commits(repo_root: Path) -> int | None:
    result = _run_git(["rev-list", "--count", "HEAD"], repo_root)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def collect_build_inputs(repo_root: Path, plan: BuildPlan) -> BuildInputs:
    return BuildInputs(
        plan=plan,
        commits_in_window=count_commits_in_window(
            repo_root,
            max_commits=plan.commits_cap,
            since=plan.since,
        ),
        tracked_source_files=count_tracked_source_files(repo_root),
        total_commits=count_total_commits(repo_root),
    )


def _pick_anchor(commits: int, files: int) -> tuple[int, int, float, float]:
    if commits > 30_000 or files > 25_000:
        return _ANCHORS[2]
    if commits < 8_000 and files < 6_000:
        return _ANCHORS[0]
    return _ANCHORS[1]


def _scale_factor(commits: int, files: int, ref_c: int, ref_f: int) -> float:
    commit_ratio = max(commits, 1) / max(ref_c, 1)
    file_ratio = max(files, 50) / max(ref_f, 50)
    return (commit_ratio**0.85) * (file_ratio**0.55)


def estimate_build(inputs: BuildInputs) -> dict[str, Any]:
    commits = max(inputs.commits_in_window, 1)
    files = max(inputs.tracked_source_files, 50)
    ref_c, ref_f, ref_build, ref_db = _pick_anchor(commits, files)
    scale = _scale_factor(commits, files, ref_c, ref_f)

    build_mid = ref_build * scale
    db_mid = ref_db * (scale**0.92)

    if build_mid < 2.0:
        build_mid = max(0.5, commits * 0.015 + files * 0.08)
        db_mid = max(0.5, files * 0.006 + commits * 0.001)

    # Wider band on extrapolation away from anchors.
    distance = abs(math.log10(commits / ref_c)) + abs(math.log10(files / ref_f)) * 0.5
    spread = min(0.45, 0.12 + distance * 0.08)
    build_low = build_mid * (1.0 - spread)
    build_high = build_mid * (1.0 + spread)
    db_low = db_mid * (1.0 - spread * 0.7)
    db_high = db_mid * (1.0 + spread * 0.7)

    confidence = "high"
    if inputs.commits_in_window == 0 or inputs.tracked_source_files == 0:
        confidence = "low"
    elif distance > 0.6:
        confidence = "medium"

    return {
        "profile": inputs.plan.profile,
        "commits_cap": inputs.plan.commits_cap,
        "commits_in_window": inputs.commits_in_window,
        "since": inputs.plan.since,
        "shards": inputs.plan.shards,
        "tracked_source_files": inputs.tracked_source_files,
        "total_commits": inputs.total_commits,
        "build_seconds": {
            "low": round(build_low, 1),
            "mid": round(build_mid, 1),
            "high": round(build_high, 1),
        },
        "graph_db_mb": {
            "low": round(db_low, 1),
            "mid": round(db_mid, 1),
            "high": round(db_high, 1),
        },
        "build_human": format_duration(build_mid),
        "build_range_human": f"{format_duration(build_low)} – {format_duration(build_high)}",
        "graph_db_human": format_disk(db_mid),
        "graph_db_range_human": f"{format_disk(db_low)} – {format_disk(db_high)}",
        "confidence": confidence,
        "method": "anchor_scaled_v1",
    }


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 7_200:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3_600:.1f} h"


def format_disk(mb: float) -> str:
    if mb < 1:
        return "<1 MB"
    if mb < 1_024:
        return f"~{mb:.0f} MB"
    return f"~{mb / 1_024:.1f} GB"


def graph_age_hours(repo_root: Path, last_commit_hash: str | None) -> int | None:
    if not last_commit_hash:
        return None
    result = _run_git(["show", "-s", "--format=%ct", last_commit_hash], repo_root)
    if result.returncode != 0:
        return None
    try:
        import time

        age_seconds = max(0, int(time.time()) - int(result.stdout.strip()))
    except ValueError:
        return None
    return age_seconds // 3_600


def gather_doctor_report(
    repo_root: Path,
    *,
    profile: str | None = None,
    commits: int | None = None,
    since: str | None = None,
    shards: int | None = None,
) -> dict[str, Any]:
    from .store import Store

    store = Store(repo_root)
    try:
        stats = store.graph_stats()
        last_hash = store.get_meta("last_commit_hash")
        last_profile = read_build_profile(store)
        meta = {
            "repo": str(repo_root),
            "last_build_commits": store.get_meta("last_build_commits"),
            "total_commits_scanned": store.get_meta("total_commits_scanned"),
            "build_strategy": store.get_meta("build_strategy"),
            "last_build_since": store.get_meta("last_build_since"),
            "last_commit_hash": last_hash,
            "build_status": store.get_meta("build_status"),
        }
    finally:
        store.close()

    plan = resolve_build_plan(
        repo_root,
        profile=profile,
        commits=commits,
        since=since,
        shards=shards,
    )
    inputs = collect_build_inputs(repo_root, plan)
    estimate = estimate_build(inputs)
    if last_profile and last_profile.get("total_sec"):
        estimate["last_build_seconds"] = last_profile["total_sec"]
        estimate["last_build_human"] = format_duration(float(last_profile["total_sec"]))

    report = {**stats, **meta, "graph_age_hours": graph_age_hours(repo_root, last_hash)}
    report["build_estimate"] = estimate
    report["build_plan"] = {
        "profile": plan.profile,
        "commits_cap": plan.commits_cap,
        "since": plan.since,
        "shards": plan.shards,
        "profiles_available": sorted(PROFILES.keys()),
    }
    from .spec_drift import check_spec_drift
    from .staleness import gather_staleness_report
    from .symbols import symbol_index_mode, treesitter_installed, use_treesitter_for_symbols
    from .watcher_health import snapshot as watcher_snapshot

    report["spec_drift"] = check_spec_drift(repo_root)
    report["symbol_index"] = {
        "mode": symbol_index_mode(),
        "treesitter_installed": treesitter_installed(),
        "treesitter_enabled": use_treesitter_for_symbols(),
    }
    if not use_treesitter_for_symbols():
        report["symbol_index"]["warning"] = (
            "Running in regex (approximate) symbol mode. "
            "Install `pip install -e '.[treesitter]'` for best search/symbol quality."
        )
    report["watcher"] = watcher_snapshot()
    store = Store(repo_root, readonly=True)
    try:
        report["staleness"] = gather_staleness_report(store, repo_root, profile_name=profile)
    finally:
        store.close()
    return report
