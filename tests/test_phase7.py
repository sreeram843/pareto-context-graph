"""Phase 7 operational hardening tests."""

from __future__ import annotations

import hashlib
import json
import threading
import time

from pareto_context_graph.deadlines import clear_current_cancel_event, set_current_cancel_event
from pareto_context_graph.graph import build_graph
from pareto_context_graph.hooks import load_hooks
from pareto_context_graph.pool import StorePool
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.signing import sign_file, verify_file
from pareto_context_graph.store import Store


def test_store_pool_concurrent_reads_and_writer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    writer = Store(repo)
    for idx in range(100):
        writer.upsert_file(f"src/f{idx}.py")
        if idx > 0:
            writer.record_co_change(f"src/f{idx - 1}.py", f"src/f{idx}.py")
    writer.commit()
    writer.close()

    pool = StorePool(repo, pool_size=8)
    errors: list[str] = []
    latencies: list[float] = []

    def reader() -> None:
        try:
            for _ in range(20):
                start = time.perf_counter()
                with pool.read() as store:
                    store.file_count()
                latencies.append(time.perf_counter() - start)
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))

    def writer_fn() -> None:
        try:
            for idx in range(10):
                with pool.write() as store:
                    store.log_feedback("q", f"src/f{idx}.py", returned=True, used=False)
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))

    threads = [threading.Thread(target=reader) for _ in range(32)]
    threads.append(threading.Thread(target=writer_fn))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    pool.close()
    assert not errors, errors
    ordered = sorted(latencies)
    p99 = ordered[int(0.99 * (len(ordered) - 1))]
    assert p99 < 0.05


def test_context_timeout_sets_truncated(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=200, files=40, seed=3)
    store = build_graph(repo, max_commits=300)
    store.close()

    start = time.perf_counter()
    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "context",
                "files": ["src/a.py"],
                "query": "routing authentication middleware",
                "tier": 1,
                "token_budget": 5000,
                "timeout_ms": 1,
            },
        )
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0
    assert payload.get("truncated") is True
    assert "request_id" in payload


def test_hook_policy_allowlist(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks = repo / ".pareto-context-graph" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "custom.py"
    hook.write_text("def pre_context(payload):\n    return payload\n")

    policy = repo / ".pareto-context-graph" / "policy.json"
    policy.write_text(json.dumps({"allowed_hook_sha256": ["deadbeef"]}))

    assert load_hooks(repo) == []

    digest = hashlib.sha256(hook.read_bytes()).hexdigest()
    policy.write_text(json.dumps({"allowed_hook_sha256": [digest]}))
    assert len(load_hooks(repo)) == 1


def test_snapshot_sign_and_verify(tmp_path, monkeypatch):
    archive = tmp_path / "snap.tar.gz"
    archive.write_bytes(b"payload")
    monkeypatch.setenv("PCG_SNAPSHOT_KEY", "test-secret")
    sign_file(archive)
    assert verify_file(archive) is True
    archive.write_bytes(b"tampered")
    assert verify_file(archive) is False


def test_mcp_cancel_request_truncates_context(synthetic_repo_factory):
    import threading

    from pareto_context_graph.cancellation import cancel, clear, register

    repo = synthetic_repo_factory(commits=200, files=40, seed=9)
    store = build_graph(repo, max_commits=300)
    store.close()

    rpc_id = "rpc-42"
    event = register(rpc_id)
    set_current_cancel_event(event)

    def _cancel_soon() -> None:
        time.sleep(0.05)
        cancel(rpc_id)

    threading.Thread(target=_cancel_soon).start()
    try:
        payload = json.loads(
            _handle_tool_call(
                repo,
                "pareto_context_graph",
                {
                    "command": "context",
                    "files": ["src/a.py"],
                    "query": "authentication routing middleware validation",
                    "tier": 1,
                    "token_budget": 5000,
                    "timeout_ms": 30_000,
                },
            )
        )
    finally:
        clear(rpc_id)
        clear_current_cancel_event()

    assert payload.get("truncated") is True or payload.get("context_files") is not None


def test_metrics_prometheus_text():
    from pareto_context_graph.metrics import METRICS

    METRICS.inc("cgmcp_test_counter_total", value=1.0, kind="unit")
    text = METRICS.prometheus_text()
    assert "cgmcp_test_counter_total" in text
