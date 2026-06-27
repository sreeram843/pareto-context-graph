#!/usr/bin/env bash
# Phase 0: clone benchmark repos, build graphs, record pins + stats.
# See docs/BENCHMARK_REPOS.md and docs/PHASES.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BENCH_DIR="${BENCH_DIR:-$ROOT/bench}"
PINS="$ROOT/tests/eval/pins.json"
RESULTS="$ROOT/tests/eval/bench_results.json"
PCG="${PCG:-}"
TIER="${TIER:-1}"

usage() {
  cat <<'EOF'
Usage: scripts/bench_setup.sh [options]

Clone OSS benchmark repos under bench/ (gitignored), build pareto-context-graph indexes,
and write tests/eval/bench_results.json with SHAs and timings.

Options:
  --tier N       Tier to set up: 1 (default), 2, 3, or all
  --bench-dir D  Clone destination (default: ./bench)
  --skip-clone   Only build graphs for repos already cloned

Makefile equivalent: `make bench-setup TIER=2 SKIP_CLONE=1`
  --update-pins  Write pinned SHAs back into tests/eval/pins.json
  -h, --help     Show this help

Examples:
  scripts/bench_setup.sh                    # fastapi + httpx (T1)
  scripts/bench_setup.sh --tier 2         # kubernetes
  scripts/bench_setup.sh --tier 2 --skip-clone
  make bench-setup TIER=2 SKIP_CLONE=1
  scripts/bench_setup.sh --tier all --update-pins
EOF
  exit "${1:-0}"
}

SKIP_CLONE=0
UPDATE_PINS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier) TIER="$2"; shift 2 ;;
    --bench-dir) BENCH_DIR="$2"; shift 2 ;;
    --skip-clone) SKIP_CLONE=1; shift ;;
    --update-pins) UPDATE_PINS=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

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

repo_in_tier() {
  local key="$1"
  local tier="$2"
  python3 -c "
import json, sys
pins = json.load(open('$PINS'))
r = pins['repos'].get('$key')
sys.exit(0 if r and int(r.get('tier', 0)) in [int(t) for t in '$tier'.replace('all','1,2,3').split(',')] else 1)
" 2>/dev/null || return 1
}

should_run() {
  local key="$1"
  case "$TIER" in
    all) return 0 ;;
    1) repo_in_tier "$key" 1 ;;
    2) repo_in_tier "$key" 2 ;;
    3) repo_in_tier "$key" 3 ;;
    *) echo "invalid --tier: $TIER" >&2; exit 1 ;;
  esac
}

clone_repo() {
  local key="$1"
  local url depth
  url="$(python3 -c "import json; print(json.load(open('$PINS'))['repos']['$key']['url'])")"
  local dest="$BENCH_DIR/$key"
  mkdir -p "$BENCH_DIR"

  if [[ -d "$dest/.git" ]]; then
    log "clone exists: $dest (fetching)"
    git -C "$dest" fetch --depth=5000 origin 2>/dev/null || git -C "$dest" fetch origin
    return
  fi

  case "$key" in
    fastapi|httpx)
      depth=5000
      log "cloning $key (depth=$depth) -> $dest"
      git clone --depth="$depth" "$url" "$dest"
      ;;
    kubernetes|linux)
      log "cloning $key (filter=blob:none) -> $dest"
      git clone --filter=blob:none "$url" "$dest"
      ;;
    *)
      log "cloning $key -> $dest"
      git clone "$url" "$dest"
      ;;
  esac
}

build_repo() {
  local key="$1"
  local dest="$BENCH_DIR/$key"
  local profile commits since shards extra_args=()

  case "$key" in
    fastapi|httpx)
      profile="tiny"
      commits=5000
      ;;
    kubernetes)
      profile="huge"
      since="12 months ago"
      commits=50000
      shards=4
      extra_args=(--since "$since" --shards "$shards")
      ;;
    linux)
      profile="huge-full"
      since="24 months ago"
      commits=100000
      shards=8
      extra_args=(--since "$since" --shards "$shards")
      ;;
    *)
      profile="medium"
      commits=5000
      ;;
  esac

  log "building graph: $key (profile=$profile commits=$commits)"
  local t0 t1 elapsed
  t0=$(date +%s)
  if [[ ${#extra_args[@]} -gt 0 ]]; then
    (cd "$dest" && "$PCG" build --profile "$profile" --commits "$commits" "${extra_args[@]}") >&2
  else
    (cd "$dest" && "$PCG" build --profile "$profile" --commits "$commits") >&2
  fi
  t1=$(date +%s)
  elapsed=$((t1 - t0))

  local sha tracked db_size stats_tmp
  stats_tmp="$(mktemp)"
  trap 'rm -f "$stats_tmp"' RETURN

  sha="$(git -C "$dest" rev-parse HEAD)"
  tracked="$(git -C "$dest" ls-files | wc -l | tr -d ' ')"
  db_size="0"
  if [[ -f "$dest/.pareto-context-graph/graph.db" ]]; then
    db_size="$(stat -f%z "$dest/.pareto-context-graph/graph.db" 2>/dev/null || stat -c%s "$dest/.pareto-context-graph/graph.db")"
  fi
  (cd "$dest" && "$PCG" stats > "$stats_tmp" 2>/dev/null) || echo '{}' > "$stats_tmp"

  python3 - "$key" "$sha" "$elapsed" "$tracked" "$db_size" "$stats_tmp" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
key, sha, elapsed, tracked, db_size, stats_path = sys.argv[1:7]
try:
    stats = json.loads(Path(stats_path).read_text())
except (json.JSONDecodeError, OSError):
    stats = {}
entry = {
    "repo_key": key,
    "path": f"bench/{key}",
    "sha": sha,
    "build_seconds": int(elapsed),
    "tracked_files": int(tracked),
    "graph_db_bytes": int(db_size),
    "stats": stats,
    "built_at": datetime.now(timezone.utc).isoformat(),
}
print(json.dumps(entry))
PY
}

merge_results() {
  local new_entry="$1"
  python3 - "$RESULTS" "$new_entry" <<'PY'
import json, sys
from pathlib import Path
results_path = Path(sys.argv[1])
new_entry = json.loads(sys.argv[2])
data = {"repos": [], "updated_at": new_entry.get("built_at")}
if results_path.exists():
    try:
        data = json.loads(results_path.read_text())
    except json.JSONDecodeError:
        pass
repos = {r["repo_key"]: r for r in data.get("repos", [])}
repos[new_entry["repo_key"]] = new_entry
data["repos"] = sorted(repos.values(), key=lambda r: r["repo_key"])
data["updated_at"] = new_entry["built_at"]
results_path.parent.mkdir(parents=True, exist_ok=True)
results_path.write_text(json.dumps(data, indent=2) + "\n")
PY
}

update_pins_file() {
  python3 - "$PINS" "$RESULTS" <<'PY'
import json, sys
from pathlib import Path
pins_path = Path(sys.argv[1])
results_path = Path(sys.argv[2])
pins = json.loads(pins_path.read_text())
results = json.loads(results_path.read_text())
by_key = {r["repo_key"]: r for r in results.get("repos", [])}
for key, meta in pins.get("repos", {}).items():
    if key in by_key:
        r = by_key[key]
        meta["sha"] = r["sha"]
        meta["pinned_at"] = r.get("built_at")
        meta["build_seconds"] = r.get("build_seconds")
        meta["tracked_files"] = r.get("tracked_files")
        meta["graph_db_bytes"] = r.get("graph_db_bytes")
pins_path.write_text(json.dumps(pins, indent=2) + "\n")
print(f"updated {pins_path}")
PY
}

main() {
  resolve_cgmcp
  log "using PCG=$PCG"
  log "bench dir: $BENCH_DIR tier: $TIER"

  local keys=()
  while IFS= read -r key; do
    keys+=("$key")
  done < <(python3 -c "import json; print('\n'.join(json.load(open('$PINS'))['repos']))") || true

  for key in "${keys[@]}"; do
    if ! should_run "$key"; then
      continue
    fi
    if [[ "$SKIP_CLONE" -eq 0 ]]; then
      clone_repo "$key"
    fi
  done

  for key in "${keys[@]}"; do
    if ! should_run "$key"; then
      continue
    fi
    if [[ ! -d "$BENCH_DIR/$key/.git" ]]; then
      echo "error: missing clone $BENCH_DIR/$key (run without --skip-clone)" >&2
      exit 1
    fi
    entry="$(build_repo "$key")" || { echo "error: build failed for $key" >&2; exit 1; }
    merge_results "$entry"
    log "recorded $key in $RESULTS"
  done

  if [[ "$UPDATE_PINS" -eq 1 ]]; then
    update_pins_file
  fi

  log "done. Results: $RESULTS"
  log "Next: make eval REPOS=fastapi=$BENCH_DIR/fastapi"
}

main
