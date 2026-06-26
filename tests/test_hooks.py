from __future__ import annotations

import json

from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call


def test_secret_redaction_in_context(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    # minimal git repo with a secret-like string
    import subprocess
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)

    src = repo / "src"
    src.mkdir()
    (src / "a.py").write_text("API_KEY=\"secret123\"\nprint('x')\n")
    (src / "b.py").write_text("print('y')\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    store = build_graph(repo, max_commits=10)
    store.close()

    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "context",
                "files": ["src/a.py"],
                "tier": 3,
                "token_budget": 5000,
            },
        )
    )

    serialized = json.dumps(payload)
    assert "secret123" not in serialized
