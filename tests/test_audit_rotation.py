"""Audit log rotation (Phase 14.4)."""

from __future__ import annotations

import json
from pathlib import Path

from pareto_context_graph.audit import (
    DEFAULT_AUDIT_MAX_BYTES,
    DEFAULT_AUDIT_MAX_FILES,
    _rotate_audit_log,
    audit_rotation_config,
    log_audit_event,
)


def _write_event(repo: Path, *, command: str = "context", query: str = "x") -> None:
    log_audit_event(
        repo,
        command=command,
        query=query,
        returned_paths=1,
        tokens_used=10,
        request_id="req",
    )


def test_audit_rotation_config_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PCG_AUDIT_ROTATION", raising=False)
    monkeypatch.delenv("PCG_AUDIT_MAX_BYTES", raising=False)
    monkeypatch.delenv("PCG_AUDIT_MAX_FILES", raising=False)
    cfg = audit_rotation_config(tmp_path)
    assert cfg["enabled"] is True
    assert cfg["max_bytes"] == DEFAULT_AUDIT_MAX_BYTES
    assert cfg["max_files"] == DEFAULT_AUDIT_MAX_FILES


def test_audit_rotation_config_from_policy(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PCG_AUDIT_MAX_BYTES", raising=False)
    monkeypatch.delenv("PCG_AUDIT_MAX_FILES", raising=False)
    policy_dir = tmp_path / ".pareto-context-graph"
    policy_dir.mkdir()
    policy_dir.joinpath("policy.json").write_text(
        json.dumps({"audit": {"max_bytes": 128, "max_files": 3}}),
        encoding="utf-8",
    )
    cfg = audit_rotation_config(tmp_path)
    assert cfg == {"enabled": True, "max_bytes": 128, "max_files": 3}


def test_audit_rotation_config_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCG_AUDIT_MAX_BYTES", "256")
    monkeypatch.setenv("PCG_AUDIT_MAX_FILES", "4")
    cfg = audit_rotation_config(tmp_path)
    assert cfg == {"enabled": True, "max_bytes": 256, "max_files": 4}


def test_rotate_audit_log_direct(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    path.write_text("a\n" * 4, encoding="utf-8")
    assert _rotate_audit_log(path, max_bytes=4, max_files=3) is True
    assert not path.exists() or path.read_text(encoding="utf-8") == ""
    assert (tmp_path / "audit.jsonl.1").read_text(encoding="utf-8") == "a\n" * 4

    path.write_text("b\n", encoding="utf-8")
    _rotate_audit_log(path, max_bytes=1, max_files=3)
    path.write_text("c\n", encoding="utf-8")
    _rotate_audit_log(path, max_bytes=1, max_files=3)
    path.write_text("d\n", encoding="utf-8")
    _rotate_audit_log(path, max_bytes=1, max_files=3)
    assert not (tmp_path / "audit.jsonl.3").exists()
    assert (tmp_path / "audit.jsonl.2").exists()


def test_audit_rotation_rotates_and_prunes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCG_AUDIT_MAX_BYTES", "80")
    monkeypatch.setenv("PCG_AUDIT_MAX_FILES", "3")
    repo = tmp_path / "repo"
    repo.mkdir()
    audit = repo / ".pareto-context-graph" / "audit.jsonl"

    for idx in range(12):
        _write_event(repo, query=f"query-{idx}")

    assert audit.exists()
    assert (repo / ".pareto-context-graph" / "audit.jsonl.1").exists()
    assert (repo / ".pareto-context-graph" / "audit.jsonl.2").exists()
    assert not (repo / ".pareto-context-graph" / "audit.jsonl.3").exists()

    all_lines = []
    for suffix in ("", ".1", ".2"):
        part = repo / ".pareto-context-graph" / f"audit.jsonl{suffix}"
        if part.exists():
            all_lines.extend(part.read_text(encoding="utf-8").strip().splitlines())
    # Each line exceeds max_bytes, so only max_files segments are retained.
    assert len(all_lines) == 3


def test_audit_rotation_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCG_AUDIT_ROTATION", "0")
    repo = tmp_path / "repo"
    repo.mkdir()
    audit = repo / ".pareto-context-graph" / "audit.jsonl"
    for idx in range(20):
        _write_event(repo, query=f"big-query-{idx}")
    assert audit.exists()
    assert not (repo / ".pareto-context-graph" / "audit.jsonl.1").exists()


def test_custom_audit_log_path_rotation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCG_AUDIT_MAX_BYTES", "60")
    monkeypatch.setenv("PCG_AUDIT_MAX_FILES", "2")
    custom = tmp_path / "logs" / "team-audit.jsonl"
    monkeypatch.setenv("PCG_AUDIT_LOG", str(custom))
    repo = tmp_path / "repo"
    repo.mkdir()
    for idx in range(8):
        _write_event(repo, query=f"path-{idx}")
    assert custom.exists()
    assert (tmp_path / "logs" / "team-audit.jsonl.1").exists()
