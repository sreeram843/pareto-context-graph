from __future__ import annotations

import json

from code_graph_mcp.cli import cmd_learn
from code_graph_mcp.store import Store


class _Args:
    repo = None


def test_learn_writes_weights(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    store = Store(repo)
    store.log_feedback("q", "src/a.py", returned=True, used=True)
    store.log_feedback("q", "src/a.py", returned=True, used=True)
    store.log_feedback("q", "src/b.py", returned=True, used=False)
    store.close()

    monkeypatch.chdir(repo)
    cmd_learn(_Args())

    weights = json.loads((repo / ".code-graph" / "weights.json").read_text())
    assert "src/a.py" in weights
    assert "src/b.py" in weights
    assert weights["src/a.py"] > weights["src/b.py"]
