#!/usr/bin/env bash
# T3 linux benchmark: clone (if needed) → build → latency bench.
# See docs/BENCHMARK_REPOS.md. Expect hours on first run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BENCH_DIR="${BENCH_DIR:-$ROOT/bench}"
LINUX="$BENCH_DIR/linux"
LOG="${LOG:-/tmp/linux-bench.log}"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }

exec > >(tee -a "$LOG") 2>&1

if [[ ! -d "$LINUX/.git" ]]; then
  log "cloning torvalds/linux (filter=blob:none)..."
  git clone --filter=blob:none --progress https://github.com/torvalds/linux.git "$LINUX"
elif ! git -C "$LINUX" rev-parse HEAD >/dev/null 2>&1; then
  log "resuming incomplete clone..."
  git -C "$LINUX" fetch --progress origin
  git -C "$LINUX" checkout -f main 2>/dev/null || git -C "$LINUX" checkout -f master
fi

SHA="$(git -C "$LINUX" rev-parse HEAD)"
COMMITS="$(git -C "$LINUX" log --no-merges --since='24 months ago' -100000 --oneline | wc -l | tr -d ' ')"
log "clone ready: $SHA ($COMMITS non-merge commits in 24mo)"

log "building graph (100k cap, 24mo, 8 shards)..."
cd "$ROOT"
make bench-setup TIER=3 SKIP_CLONE=1

log "running latency bench..."
SKIP_BUILD=1 SKIP_INCREMENTAL=1 make bench-huge REPOS=linux="$LINUX"

log "done — see tests/eval/bench_results.json and docs/BENCHMARKS.md"
