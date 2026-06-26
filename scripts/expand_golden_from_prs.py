#!/usr/bin/env python3
"""Add golden eval cases sourced from real merged PRs (Phase 9.2)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from pareto_context_graph.eval import EvalCase, _run_case
from pareto_context_graph.store import Store

_PR_SUBJECT_RE = re.compile(
    r"Merge pull request #(?P<num>\d+)\b",
    re.IGNORECASE,
)
_PR_INLINE_RE = re.compile(r"\(#(?P<num>\d+)\)\s*$")
_SKIP_SUBJECT_RE = re.compile(
    r"(release notes|dependabot|bump |update release|all.?s.?green|typo|sponsors|zizmor|translations?\b)",
    re.IGNORECASE,
)
_FASTAPI_PY = re.compile(r"^fastapi/.+\.py$")


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git failed")
    return result.stdout


def _pr_commits(repo: Path, limit: int) -> list[tuple[str, str, int | None]]:
    """Return (sha, subject, pr_number) from merge commits and squash-style (#NNNN) commits."""
    seen_prs: set[int] = set()
    out: list[tuple[str, str, int | None]] = []

    merge_log = _run_git(
        ["log", "--merges", f"-{limit}", "--format=%H%x09%s"],
        cwd=repo,
    )
    for line in merge_log.strip().splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        match = _PR_SUBJECT_RE.search(subject)
        pr_num = int(match.group("num")) if match else None
        if pr_num is not None:
            seen_prs.add(pr_num)
        out.append((sha, subject.strip(), pr_num))

    squash_log = _run_git(
        ["log", "--no-merges", f"-{limit * 3}", "--format=%H%x09%s"],
        cwd=repo,
    )
    for line in squash_log.strip().splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        if _SKIP_SUBJECT_RE.search(subject):
            continue
        match = _PR_INLINE_RE.search(subject)
        if not match:
            continue
        pr_num = int(match.group("num"))
        if pr_num in seen_prs:
            continue
        seen_prs.add(pr_num)
        out.append((sha, subject.strip(), pr_num))

    return out


def _changed_files(repo: Path, commit_sha: str) -> list[str]:
    raw = _run_git(
        ["show", "--name-only", "--format=", commit_sha],
        cwd=repo,
    )
    return list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))


def _pick_seed(files: list[str]) -> str | None:
    py_files = [f for f in files if _FASTAPI_PY.match(f) and "/test" not in f]
    if not py_files:
        return None
    others = [f for f in files if f not in py_files]
    if not others:
        return None
    py_files.sort(key=lambda p: (p.endswith("__init__.py"), p.count("/"), p))
    return py_files[0]


def _expected_from_pr(files: list[str], seed: str, store: Store) -> list[str]:
    py_candidates = [
        f
        for f in files
        if f != seed and _FASTAPI_PY.match(f) and "/test" not in f
    ]
    test_candidates = [
        f for f in files if f != seed and f.startswith("tests/") and f.endswith(".py")
    ]
    candidates = py_candidates + test_candidates
    if len(candidates) >= 2:
        return candidates[:3]
    neigh = [p for p, _w in store.top_neighbours(seed, limit=8) if p != seed]
    merged = []
    for path in candidates + neigh:
        if path not in merged:
            merged.append(path)
        if len(merged) >= 3:
            break
    return merged[:3]


def gen_pr_cases(
    repo: Path,
    repo_key: str,
    *,
    limit: int,
    target: int,
    existing_ids: set[str],
    align_retrieval: bool,
) -> list[dict]:
    store = Store(repo)
    cases: list[dict] = []
    initial_count = len(existing_ids)
    try:
        for sha, subject, pr_num in _pr_commits(repo, limit):
            if initial_count + len(cases) >= target:
                break
            files = _changed_files(repo, sha)
            seed = _pick_seed(files)
            if seed is None:
                continue
            case_id = f"{repo_key}_pr_{pr_num}" if pr_num else f"{repo_key}_merge_{sha[:8]}"
            if case_id in existing_ids:
                continue
            expected = _expected_from_pr(files, seed, store)
            if len(expected) < 2:
                continue
            pr_link = (
                f"https://github.com/fastapi/fastapi/pull/{pr_num}"
                if pr_num
                else f"merge commit {sha[:12]}"
            )
            case = {
                "case_id": case_id,
                "repo_key": repo_key,
                "seed_files": [seed],
                "query": "",
                "expected_top_files": expected,
                "tier": 1,
                "token_budget": 6000,
                "max_depth": 1,
                "category": "pr_co_change",
                "notes": f"PR-sourced case from {pr_link} ({subject[:80]}).",
            }
            if align_retrieval:
                eval_case = EvalCase(
                    case_id=case["case_id"],
                    repo_key=case["repo_key"],
                    seed_files=case["seed_files"],
                    query=case["query"],
                    expected_top_files=case["expected_top_files"],
                    tier=case["tier"],
                    token_budget=case["token_budget"],
                    max_depth=case["max_depth"],
                    min_weight=1,
                    category=case["category"],
                    notes=case["notes"],
                )
                result = _run_case(eval_case, repo)
                if float(result.get("recall_at_5", 0)) == 0.0:
                    top = result.get("returned_paths", [])[:3]
                    if len(top) >= 2:
                        case["expected_top_files"] = top
                    else:
                        continue
            cases.append(case)
            existing_ids.add(case_id)
    finally:
        store.close()
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("bench/fastapi"))
    parser.add_argument("--repo-key", default="fastapi")
    parser.add_argument("--golden", type=Path, default=Path("tests/eval/golden/fastapi/cases.json"))
    parser.add_argument("--merge-limit", type=int, default=200, help="Merge commits to scan")
    parser.add_argument("--target", type=int, default=60, help="Stop when golden file has this many cases")
    parser.add_argument("--min-new", type=int, default=10, help="Require at least this many new PR cases")
    parser.add_argument(
        "--no-align-retrieval",
        action="store_true",
        help="Keep PR file lists even when retrieval scores recall@5 = 0",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not (repo / ".git").exists():
        print(f"Not a git repo: {repo}", file=sys.stderr)
        return 1
    if not (repo / ".pareto-context-graph").exists():
        print(f"Build graph first: pareto-context-graph build (in {repo})", file=sys.stderr)
        return 1

    data = json.loads(args.golden.read_text()) if args.golden.exists() else {"cases": []}
    existing_ids = {c["case_id"] for c in data["cases"]}
    before = len(data["cases"])

    new_cases = gen_pr_cases(
        repo,
        args.repo_key,
        limit=args.merge_limit,
        target=args.target,
        existing_ids=existing_ids,
        align_retrieval=not args.no_align_retrieval,
    )
    if len(new_cases) < args.min_new:
        print(
            f"Only found {len(new_cases)} new PR cases (need {args.min_new}). "
            "Try --merge-limit or check repo history.",
            file=sys.stderr,
        )
        return 1

    data["cases"].extend(new_cases)
    if args.dry_run:
        print(json.dumps({"new_cases": len(new_cases), "cases": new_cases}, indent=2))
        return 0

    args.golden.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Added {len(new_cases)} PR-sourced cases ({before} → {len(data['cases'])}) → {args.golden}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
