#!/usr/bin/env bash
# Pre-flight gate before long T2/T3 benchmarks (matches CI quality + eval workflows).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PCG="${PCG:-}"
PYTHON="${PYTHON:-}"
BENCH_DIR="${BENCH_DIR:-$ROOT/bench}"
SKIP_EVAL="${SKIP_EVAL:-0}"
SKIP_BUILD_T1="${SKIP_BUILD_T1:-0}"
SKIP_FEEDBACK_REPLAY="${SKIP_FEEDBACK_REPLAY:-0}"

log() { printf '[pre-bench] %s\n' "$*"; }

if [[ -x "$ROOT/.venv/bin/pareto-context-graph" ]]; then
  PCG="${PCG:-$ROOT/.venv/bin/pareto-context-graph}"
  PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
else
  PCG="${PCG:-pareto-context-graph}"
  PYTHON="${PYTHON:-python3}"
fi

export PCG_EDGE_DECAY=0

run_eval_gate() {
  local label="$1"
  shift
  local attempt
  for attempt in 1 2; do
    if "$@"; then
      return 0
    fi
    if [[ "$attempt" -eq 2 ]]; then
      log "$label failed after 2 attempts"
      return 1
    fi
    log "$label failed (attempt $attempt) — resetting bench repos and retrying"
    reset_bench_repos
  done
}

reset_bench_repos() {
  log "reset T1 bench learning state + SQLite pools"
  PYTHONPATH=. "$PYTHON" - "$BENCH_DIR" <<'PY'
import sys
from pathlib import Path

from pareto_context_graph.feedback import clear_learning_state
from pareto_context_graph.pool import close_store_pool
from pareto_context_graph.repo_caches import invalidate_caches

bench_dir = Path(sys.argv[1])
for key in ("fastapi", "httpx"):
    repo = bench_dir / key
    if (repo / ".pareto-context-graph" / "graph.db").exists():
        close_store_pool(repo.resolve())
        clear_learning_state(repo.resolve())
invalidate_caches()
PY
}

log "ruff check"
"$ROOT/.venv/bin/ruff" check src/pareto_context_graph
"$ROOT/.venv/bin/ruff" format --check src/pareto_context_graph tests

log "mypy"
"$ROOT/.venv/bin/mypy" src/pareto_context_graph

log "bench-stress (synthetic gates)"
make bench-stress

if [[ "$SKIP_EVAL" == "1" ]]; then
  log "SKIP_EVAL=1 — running pytest only"
  "$ROOT/.venv/bin/pytest" --cov=pareto_context_graph --cov-report=term-missing -q
  log "pre-bench OK (eval skipped)"
  exit 0
fi

T1_REPOS="fastapi=$BENCH_DIR/fastapi httpx=$BENCH_DIR/httpx"
for key in fastapi httpx; do
  if [[ ! -d "$BENCH_DIR/$key/.git" ]]; then
    log "missing bench/$key — run: make bench-setup-t1"
    exit 1
  fi
done

if [[ "$SKIP_BUILD_T1" != "1" ]]; then
  for key in fastapi httpx; do
    graph="$BENCH_DIR/$key/.pareto-context-graph/graph.db"
    if [[ ! -f "$graph" ]]; then
      log "building T1 graph: $key"
      (cd "$BENCH_DIR/$key" && "$PCG" build --profile tiny --commits 5000)
    fi
  done
fi

# Eval gates run BEFORE pytest — pytest (especially feedback replay) can mutate
# bench/fastapi learning artifacts and cause flaky recall vs baseline.
reset_bench_repos

log "eval-check + compress (single eval run)"
run_eval_gate "eval-compress-check" make eval-compress-check REPOS="$T1_REPOS"

reset_bench_repos

log "eval-audit"
make eval-audit REPOS="$T1_REPOS"

log "pytest (full suite)"
"$ROOT/.venv/bin/pytest" --cov=pareto_context_graph --cov-report=term-missing -q \
  -k "not test_fastapi_feedback_replay_holdout_gain"

log "feedback-replay (pytest gate: non-regression + weights)"
if [[ "$SKIP_FEEDBACK_REPLAY" == "1" ]]; then
  log "SKIP_FEEDBACK_REPLAY=1 — skipping"
else
  "$ROOT/.venv/bin/pytest" -q tests/test_feedback_replay.py::test_fastapi_feedback_replay_holdout_gain
fi

log "pre-bench OK"
