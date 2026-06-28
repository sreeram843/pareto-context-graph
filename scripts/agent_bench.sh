#!/usr/bin/env bash
# Phase 1.3 — real agent A/B from claude -p transcripts.
#
# For each flow in tests/eval/flows/ground-truth.json, run a coding agent N times in
# each arm and record real tool-call / token / cost metrics parsed from stream-json:
#   - pcg arm:      claude with the pareto-context-graph MCP server available
#   - baseline arm: claude with an EMPTY mcp config (--strict-mcp-config), i.e. plain
#                   Read/Grep/Bash only — the grep-first workflow pcg aims to replace
#
# Requires: `claude` CLI authenticated, `jq`, and `pareto-context-graph` on PATH.
# Output:   tests/eval/agent-ab.json  (per-flow medians + pcg-vs-baseline reductions)
#
# Usage: scripts/agent_bench.sh [N_RUNS] [MODEL]
set -euo pipefail

N_RUNS="${1:-4}"
MODEL="${2:-sonnet}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLOWS="$ROOT/tests/eval/flows/ground-truth.json"
BENCH="$ROOT/bench"
OUT="$ROOT/tests/eval/agent-ab.json"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

command -v claude >/dev/null || { echo "ERROR: claude CLI not found" >&2; exit 2; }
command -v jq >/dev/null || { echo "ERROR: jq not found" >&2; exit 2; }

run_arm() { # repo_abs  mcp_config_json  question  out_jsonl
  local repo="$1" mcp="$2" question="$3" out="$4"
  printf '%s' "$mcp" > "$WORK/mcp.json"
  # --strict-mcp-config: use ONLY the provided config (no user/project servers leak in).
  claude -p "$question" \
    --model "$MODEL" \
    --output-format stream-json --verbose \
    --add-dir "$repo" \
    --mcp-config "$WORK/mcp.json" --strict-mcp-config \
    > "$out" 2>"$WORK/stderr.log" || {
      echo "WARN: claude run failed (see stderr)" >&2; cat "$WORK/stderr.log" >&2; }
}

results="[]"
flow_count="$(jq '.flows | length' "$FLOWS")"
for i in $(seq 0 $((flow_count - 1))); do
  flow="$(jq ".flows[$i]" "$FLOWS")"
  flow_id="$(jq -r '.flow_id' <<<"$flow")"
  repo_key="$(jq -r '.repo_key' <<<"$flow")"
  question="$(jq -r '.question' <<<"$flow")"
  repo="$BENCH/$repo_key"
  [ -d "$repo/.git" ] || { echo "skip $flow_id: bench/$repo_key not cloned" >&2; continue; }
  repo_abs="$(cd "$repo" && pwd)"

  pcg_mcp=$(jq -n --arg r "$repo_abs" \
    '{mcpServers:{pareto_context_graph:{command:"pareto-context-graph",args:["serve","--repo",$r]}}}')

  pcg_runs="$WORK/${flow_id}.pcg.jsonl.list"; : > "$pcg_runs"
  base_runs="$WORK/${flow_id}.base.jsonl.list"; : > "$base_runs"
  for n in $(seq 1 "$N_RUNS"); do
    echo ">> $flow_id pcg run $n/$N_RUNS" >&2
    run_arm "$repo_abs" "$pcg_mcp" "$question" "$WORK/${flow_id}.pcg.$n.jsonl"
    echo "$WORK/${flow_id}.pcg.$n.jsonl" >> "$pcg_runs"
    echo ">> $flow_id baseline run $n/$N_RUNS" >&2
    run_arm "$repo_abs" '{}' "$question" "$WORK/${flow_id}.base.$n.jsonl"
    echo "$WORK/${flow_id}.base.$n.jsonl" >> "$base_runs"
  done

  # Aggregate medians + reductions in Python (uses the unit-tested parser).
  flow_result="$(python3 "$ROOT/scripts/agent_bench_aggregate.py" \
    --flow-id "$flow_id" --pcg-runs "$pcg_runs" --baseline-runs "$base_runs")"
  results="$(jq --argjson r "$flow_result" '. + [$r]' <<<"$results")"
done

jq -n --argjson flows "$results" --arg model "$MODEL" --argjson n "$N_RUNS" \
  '{model:$model, runs_per_arm:$n, flows:$flows}' > "$OUT"
echo "wrote $OUT" >&2
