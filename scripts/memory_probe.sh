#!/usr/bin/env bash
# Phase 1.2 — memory-probe repo gate.
#
# A flow accuracy case is only honest if the model CANNOT already trace the repo from
# memory. This asks claude the flow question with NO tools and NO repo access, then has
# the judge (no tools, neutral cwd) score the unaided answer against ground truth.
# A repo PASSES the gate (is kept for accuracy) only if the unaided answer FAILS.
#
# Requires: `claude` CLI authenticated, `jq`, `node`.
# Output:   tests/eval/flows/memory-probe.json  (per-flow: unaided verdict + keep flag)
#
# Usage: scripts/memory_probe.sh [MODEL]
set -euo pipefail

MODEL="${1:-sonnet}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FLOWS="$ROOT/tests/eval/flows/ground-truth.json"
OUT="$ROOT/tests/eval/flows/memory-probe.json"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

command -v claude >/dev/null || { echo "ERROR: claude CLI not found" >&2; exit 2; }
command -v jq >/dev/null || { echo "ERROR: jq not found" >&2; exit 2; }

results="[]"
flow_count="$(jq '.flows | length' "$FLOWS")"
for i in $(seq 0 $((flow_count - 1))); do
  flow="$(jq ".flows[$i]" "$FLOWS")"
  flow_id="$(jq -r '.flow_id' <<<"$flow")"
  question="$(jq -r '.question' <<<"$flow")"

  # No tools, no repo: pure recall. Neutral cwd so it can't read the checkout.
  answer="$(cd "$WORK" && claude -p \
    "Answer ONLY from memory; you have no tools and no repository access. $question" \
    --model "$MODEL" --output-format json \
    --mcp-config <(echo '{}') --strict-mcp-config \
    | jq -r '.result // ""')"

  verdict="$(MODEL="$MODEL" node "$ROOT/scripts/eval_judge.mjs" \
    --flow "$flow" --answer "$answer" --mode fidelity)"
  keep="$(jq -n --argjson v "$verdict" '($v.verdict // "fail") == "fail"')"
  results="$(jq --arg id "$flow_id" --argjson v "$verdict" --argjson keep "$keep" \
    '. + [{flow_id:$id, unaided_verdict:$v, keep_for_accuracy:$keep}]' <<<"$results")"
  echo ">> $flow_id keep_for_accuracy=$keep" >&2
done

jq -n --argjson flows "$results" '{flows:$flows}' > "$OUT"
echo "wrote $OUT" >&2
