from __future__ import annotations

import random
import subprocess
from pathlib import Path


def _run_git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def create_synthetic_repo(
    repo: Path,
    *,
    commit_count: int = 200,
    file_count: int = 30,
    seed: int = 7,
) -> Path:
    """Create a deterministic git repo for graph-build tests.

    The file pair a.py/b.py co-changes in every commit to provide
    a stable high-weight edge for assertions.
    """
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "tests@example.com")
    _run_git(repo, "config", "user.name", "test-bot")

    files = [f"src/f{i}.py" for i in range(file_count)]
    files.extend(["src/a.py", "src/b.py"])

    for file_path in files:
        target = repo / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# synthetic\n", encoding="utf-8")

    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "initial")

    rng = random.Random(seed)
    for i in range(commit_count):
        changed = {"src/a.py", "src/b.py"}
        while len(changed) < min(5, len(files)):
            changed.add(rng.choice(files))

        for file_path in sorted(changed):
            target = repo / file_path
            with target.open("a", encoding="utf-8") as handle:
                handle.write(f"# c{i}\n")

        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"commit {i:04d}")

    return repo
