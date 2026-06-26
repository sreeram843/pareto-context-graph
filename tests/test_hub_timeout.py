"""Hub-seed context timeout integration on a real OSS graph (Phase 6/7).

Uses the fastapi bench when built: top hub is typically ``docs/en/mkdocs.yml``
(degree ~1100). Verifies ``timeout_ms`` stops work promptly with ``truncated: true``
instead of hanging on high-fanout seeds.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pareto_context_graph.bench import pick_hub_seed
from pareto_context_graph.deadlines import DEFAULT_TIMEOUT_MS
from pareto_context_graph.pool import get_store_pool
from pareto_context_graph.profiles import autodetect_profile
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store

FASTAPI_BENCH = Path("bench/fastapi")
FASTAPI_GRAPH = FASTAPI_BENCH / ".pareto-context-graph" / "graph.db"

CONTEXT_PHASES = frozenset({"retrieve", "hybrid", "semantic", "rank", "pack", "filter"})
MIN_HUB_DEGREE = 50
TIGHT_TIMEOUT_MS = 50
WALL_CLOCK_SLACK_SECONDS = 2.5

pytestmark = pytest.mark.skipif(
    not FASTAPI_GRAPH.exists(),
    reason="fastapi bench graph not built (run: make bench-setup TIER=1)",
)


@pytest.fixture(scope="module", autouse=True)
def _warm_store_pools() -> None:
    """Prime SQLite read pools so context timing excludes cold open."""
    for bench in (FASTAPI_BENCH, KUBERNETES_BENCH, LINUX_BENCH):
        graph = bench / ".pareto-context-graph" / "graph.db"
        if graph.exists():
            repo = bench.resolve()
            autodetect_profile(repo)
            pool = get_store_pool(repo)
            with pool.read() as store:
                store.file_count()


@pytest.fixture(scope="module")
def repo() -> Path:
    return FASTAPI_BENCH.resolve()


@pytest.fixture(scope="module")
def hub_seed(repo: Path) -> str:
    return pick_hub_seed(repo)


def _context(repo: Path, *, hub: str, timeout_ms: int, query: str = "") -> dict:
    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": [hub],
            "query": query,
            "tier": 1,
            "token_budget": 8000,
            "timeout_ms": timeout_ms,
            "session_memory": False,
        },
    )
    return json.loads(raw)


def _graph_is_usable(repo: Path) -> bool:
    store = Store(repo)
    try:
        return store.file_count() > 0 and store.edge_count() > 0
    finally:
        store.close()


def test_oss_hub_is_high_degree(repo: Path, hub_seed: str) -> None:
    store = Store(repo)
    try:
        stats = store.graph_stats()
        degree = store.node_degrees().get(hub_seed, 0)
        top_hubs = stats.get("top_hubs") or []
    finally:
        store.close()

    assert top_hubs, "graph has no hubs"
    assert hub_seed == top_hubs[0]["path"]
    assert degree >= MIN_HUB_DEGREE, (
        f"expected OSS hub degree >= {MIN_HUB_DEGREE}, got {degree} for {hub_seed}"
    )


def test_tight_deadline_truncates_without_hanging(repo: Path, hub_seed: str) -> None:
    start = time.perf_counter()
    payload = _context(repo, hub=hub_seed, timeout_ms=TIGHT_TIMEOUT_MS, query="routing")
    elapsed = time.perf_counter() - start

    assert "error" not in payload
    assert payload.get("truncated") is True or len(payload.get("context_files", [])) > 0
    assert payload.get("truncated_phase") in CONTEXT_PHASES or payload.get("context_files")
    assert payload.get("request_id")
    assert elapsed < (TIGHT_TIMEOUT_MS / 1000.0) + WALL_CLOCK_SLACK_SECONDS


def test_default_deadline_completes_with_results(repo: Path, hub_seed: str) -> None:
    start = time.perf_counter()
    payload = _context(
        repo,
        hub=hub_seed,
        timeout_ms=DEFAULT_TIMEOUT_MS,
        query="routing handler",
    )
    elapsed = time.perf_counter() - start

    assert "error" not in payload
    assert payload.get("truncated") is not True
    assert len(payload.get("context_files", [])) > 0
    assert elapsed < (DEFAULT_TIMEOUT_MS / 1000.0) + 0.5


KUBERNETES_BENCH = Path("bench/kubernetes")
KUBERNETES_GRAPH = KUBERNETES_BENCH / ".pareto-context-graph" / "graph.db"
MIN_K8S_HUB_DEGREE = 50
K8S_DEFAULT_TIMEOUT_MS = 8000
K8S_WALL_SLACK_SECONDS = 4.0


@pytest.fixture(scope="module")
def k8s_repo() -> Path:
    return KUBERNETES_BENCH.resolve()


@pytest.fixture(scope="module")
def k8s_hub_seed(k8s_repo: Path) -> str:
    return pick_hub_seed(k8s_repo)


@pytest.mark.skipif(
    not KUBERNETES_GRAPH.exists(),
    reason="kubernetes bench graph not built (run: make bench-setup TIER=2)",
)
def test_kubernetes_hub_is_high_degree(k8s_repo: Path, k8s_hub_seed: str) -> None:
    store = Store(k8s_repo)
    try:
        stats = store.graph_stats()
        degree = store.node_degrees().get(k8s_hub_seed, 0)
        top_hubs = stats.get("top_hubs") or []
    finally:
        store.close()
    assert top_hubs and k8s_hub_seed == top_hubs[0]["path"]
    assert degree >= MIN_K8S_HUB_DEGREE


@pytest.mark.skipif(
    not KUBERNETES_GRAPH.exists(),
    reason="kubernetes bench graph not built (run: make bench-setup TIER=2)",
)
def test_kubernetes_tight_deadline_truncates(k8s_repo: Path, k8s_hub_seed: str) -> None:
    start = time.perf_counter()
    payload = _context(
        k8s_repo,
        hub=k8s_hub_seed,
        timeout_ms=TIGHT_TIMEOUT_MS,
        query="kubelet",
    )
    elapsed = time.perf_counter() - start
    assert payload.get("truncated") is True or len(payload.get("context_files", [])) > 0
    if payload.get("truncated"):
        assert payload.get("truncated_phase") in CONTEXT_PHASES
    assert elapsed < (TIGHT_TIMEOUT_MS / 1000.0) + K8S_WALL_SLACK_SECONDS


@pytest.mark.skipif(
    not KUBERNETES_GRAPH.exists(),
    reason="kubernetes bench graph not built (run: make bench-setup TIER=2)",
)
def test_kubernetes_hub_context_within_deadline(k8s_repo: Path, k8s_hub_seed: str) -> None:
    """Huge-profile hub seed completes or truncates without hanging past deadline."""
    start = time.perf_counter()
    payload = _context(
        k8s_repo,
        hub=k8s_hub_seed,
        timeout_ms=K8S_DEFAULT_TIMEOUT_MS,
        query="kubelet pod",
    )
    elapsed = time.perf_counter() - start
    assert "error" not in payload
    assert payload.get("request_id")
    assert payload.get("truncated") is True or len(payload.get("context_files", [])) > 0
    assert elapsed < (K8S_DEFAULT_TIMEOUT_MS / 1000.0) + K8S_WALL_SLACK_SECONDS


LINUX_BENCH = Path("bench/linux")
LINUX_GRAPH = LINUX_BENCH / ".pareto-context-graph" / "graph.db"
MIN_LINUX_HUB_DEGREE = 50
LINUX_DEFAULT_TIMEOUT_MS = DEFAULT_TIMEOUT_MS
LINUX_WALL_SLACK_SECONDS = 2.5


@pytest.fixture(scope="module")
def linux_repo() -> Path:
    return LINUX_BENCH.resolve()


@pytest.fixture(scope="module")
def linux_hub_seed(linux_repo: Path) -> str:
    return pick_hub_seed(linux_repo)


@pytest.mark.skipif(
    not LINUX_GRAPH.exists() or not _graph_is_usable(LINUX_BENCH.resolve()),
    reason="linux bench graph not built or empty (run: make bench-linux)",
)
def test_linux_hub_is_maintainers(linux_repo: Path, linux_hub_seed: str) -> None:
    store = Store(linux_repo)
    try:
        stats = store.graph_stats()
        degree = store.file_degree(linux_hub_seed)
        top_hubs = stats.get("top_hubs") or []
    finally:
        store.close()
    assert top_hubs and linux_hub_seed == top_hubs[0]["path"]
    assert degree >= MIN_LINUX_HUB_DEGREE


@pytest.mark.skipif(
    not LINUX_GRAPH.exists() or not _graph_is_usable(LINUX_BENCH.resolve()),
    reason="linux bench graph not built or empty (run: make bench-linux)",
)
def test_linux_tight_deadline_truncates(linux_repo: Path, linux_hub_seed: str) -> None:
    start = time.perf_counter()
    payload = _context(
        linux_repo,
        hub=linux_hub_seed,
        timeout_ms=TIGHT_TIMEOUT_MS,
        query="scheduler",
    )
    elapsed = time.perf_counter() - start
    assert payload.get("truncated") is True or len(payload.get("context_files", [])) > 0
    if payload.get("truncated"):
        assert payload.get("truncated_phase") in CONTEXT_PHASES
    assert elapsed < (TIGHT_TIMEOUT_MS / 1000.0) + LINUX_WALL_SLACK_SECONDS


@pytest.mark.skipif(
    not LINUX_GRAPH.exists() or not _graph_is_usable(LINUX_BENCH.resolve()),
    reason="linux bench graph not built or empty (run: make bench-linux)",
)
def test_linux_hub_context_within_deadline(linux_repo: Path, linux_hub_seed: str) -> None:
    start = time.perf_counter()
    payload = _context(
        linux_repo,
        hub=linux_hub_seed,
        timeout_ms=LINUX_DEFAULT_TIMEOUT_MS,
        query="",
    )
    elapsed = time.perf_counter() - start
    assert "error" not in payload
    assert payload.get("request_id")
    assert payload.get("truncated") is True or len(payload.get("context_files", [])) > 0
    assert elapsed < (LINUX_DEFAULT_TIMEOUT_MS / 1000.0) + LINUX_WALL_SLACK_SECONDS
