#!/usr/bin/env bash
# Huge-repo benchmark runner (Tier 2/3). See docs/BENCHMARK_REPOS.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PCG="${PCG:-}"
RESULTS="${RESULTS:-$ROOT/tests/eval/bench_results.json}"
SKIP_BUILD="${SKIP_BUILD:-0}"
CONTEXT_ROUNDS="${CONTEXT_ROUNDS:-3}"
SKIP_INCREMENTAL="${SKIP_INCREMENTAL:-0}"

usage() {
  cat <<'EOF'
Usage: bench_huge.sh <repo_key>=<repo_path> [<repo_key>=<path> ...]
       bench_huge.sh <repo_key> <repo_path> [<repo_key> <path> ...]

Run bounded huge-profile builds and record stress metrics.

Environment:
  PCG           pareto-context-graph binary (default: .venv/bin or PATH)
  SKIP_BUILD=1    Skip build; only measure context/update latency
  CONTEXT_ROUNDS  Context latency samples per query (default: 3)
  SKIP_INCREMENTAL=1  Skip incremental update timing (recommended in CI)
  RESULTS         Output JSON path (default: tests/eval/bench_results.json)

Examples:
  ./scripts/bench_huge.sh kubernetes ~/bench/kubernetes
  SKIP_BUILD=1 ./scripts/bench_huge.sh kubernetes ~/bench/kubernetes
  make bench-huge REPOS=kubernetes=$(pwd)/bench/kubernetes
EOF
  exit 1
}

[[ $# -ge 1 ]] || usage

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

resolve_cgmcp() {
  if [[ -n "$PCG" ]]; then
    if [[ "$PCG" != /* ]] && [[ -x "$ROOT/$PCG" ]]; then
      PCG="$ROOT/$PCG"
    fi
    return
  fi
  if [[ -x "$ROOT/.venv/bin/pareto-context-graph" ]]; then
    PCG="$ROOT/.venv/bin/pareto-context-graph"
  elif command -v pareto-context-graph >/dev/null 2>&1; then
    PCG="$(command -v pareto-context-graph)"
  else
    echo "error: pareto-context-graph not found. Run: pip install -e $ROOT" >&2
    exit 1
  fi
}

run_one() {
  local key="$1"
  local path="$2"
  local profile="huge"
  local since="12 months ago"
  local commits=50000
  local shards=4

  if [[ "$key" == "linux" ]]; then
    since="24 months ago"
    commits=100000
    shards=8
  fi

  if [[ ! -d "$path/.git" ]]; then
    echo "error: not a git repo: $path" >&2
    return 1
  fi

  log "=== $key @ $path (profile=$profile commits=$commits) ==="

  if [[ "$SKIP_BUILD" != "1" ]]; then
    local t0 t1 elapsed
    t0=$(date +%s)
    (cd "$path" && "$PCG" build --profile "$profile" --since "$since" --commits "$commits" --shards "$shards")
    t1=$(date +%s)
    elapsed=$((t1 - t0))
    log "build elapsed: ${elapsed}s"
  else
    log "SKIP_BUILD=1 — measuring latency only"
  fi

  local db_size="missing"
  if [[ -f "$path/.pareto-context-graph/graph.db" ]]; then
    db_size=$(du -h "$path/.pareto-context-graph/graph.db" | cut -f1)
  fi
  log "graph.db size: $db_size"

  (cd "$path" && "$PCG" stats)
  (cd "$path" && "$PCG" doctor)

  local bench_args=(bench --key "$key" --merge-results "$RESULTS" --rounds "$CONTEXT_ROUNDS")
  if [[ "$SKIP_BUILD" != "1" ]]; then
    bench_args+=(--record-build)
  fi
  if [[ "$SKIP_INCREMENTAL" == "1" ]]; then
    bench_args+=(--skip-incremental)
  fi
  (cd "$path" && "$PCG" "${bench_args[@]}")

  log "=== done $key (results -> $RESULTS) ==="
}

resolve_cgmcp
log "using PCG=$PCG"

while [[ $# -ge 1 ]]; do
  if [[ "$1" == *=* ]]; then
    run_one "${1%%=*}" "${1#*=}"
    shift
  elif [[ $# -ge 2 ]]; then
    run_one "$1" "$2"
    shift 2
  else
    usage
  fi
done
