from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")


def _write_text(path: Path, text: str, *, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


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
    _run_git(repo, "init", "--initial-branch=main")
    for key, value in (
        ("user.email", "tests@example.com"),
        ("user.name", "test-bot"),
        ("core.fsmonitor", "false"),
        ("core.preloadindex", "false"),
        ("core.fsync", "all"),
        ("commit.gpgsign", "false"),
    ):
        _run_git(repo, "config", key, value)

    files = [f"src/f{i}.py" for i in range(file_count)]
    files.extend(["src/a.py", "src/b.py"])

    for file_path in files:
        _write_text(repo / file_path, "# synthetic\n")

    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "initial")

    rng = random.Random(seed)
    for i in range(commit_count):
        changed = {"src/a.py", "src/b.py"}
        while len(changed) < min(5, len(files)):
            changed.add(rng.choice(files))

        for file_path in sorted(changed):
            _write_text(repo / file_path, f"# c{i}\n", append=True)

        _run_git(repo, "add", ".")
        _run_git(repo, "commit", "-m", f"commit {i:04d}")

    return repo
