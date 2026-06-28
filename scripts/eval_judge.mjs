#!/usr/bin/env node
// Phase 1.4 — LLM judge. Scores an agent's flow answer against verified ground truth.
//
// Runs `claude -p` with NO tools and a NEUTRAL cwd (so the judge cannot read the repo
// and must score only the text it is given). Emits a strict JSON verdict:
//   { verdict: "pass"|"partial"|"fail", score: 0..1, missedHops: [], wrongClaims: [],
//     fabrication: bool, rationale: string }
// Confident-wrong (fabrication / wrongClaims) is penalized harder than an honest
// "could not determine".
//
// Usage: node scripts/eval_judge.mjs --flow '<flow json>' --answer '<text>' --mode e2e|fidelity
// Env:   MODEL (default "sonnet")
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const model = process.env.MODEL || "sonnet";
const mode = arg("mode", "e2e");
const answer = arg("answer", "");
let flow;
try {
  flow = JSON.parse(arg("flow", "{}"));
} catch {
  console.error("eval_judge: --flow must be valid JSON");
  process.exit(2);
}

const callPath = (flow.call_path || [])
  .map((s, i) => `${i + 1}. ${s.symbol} (${s.file}:${s.line}) — ${s.note || ""}`)
  .join("\n");
const mustHit = (flow.must_hit_symbols || []).join(", ");
const boundaries = (flow.dynamic_boundaries || []).map((b) => `- ${b}`).join("\n");

const prompt = `You are a strict evaluator of a code-tracing answer. You have NO tools and NO repo access; judge ONLY the answer text against the ground truth below.

QUESTION:
${flow.question || ""}

GROUND-TRUTH CALL PATH (verified):
${callPath}

MUST-HIT SYMBOLS: ${mustHit}

DYNAMIC BOUNDARIES (a correct answer should respect these; static-only tracing breaks here):
${boundaries}

CANDIDATE ANSWER (mode=${mode}):
"""
${answer}
"""

Score how well the answer reconstructs the real call path. Rules:
- "fail" if it misses most must-hit symbols OR makes confident wrong claims.
- "partial" if it gets the gist but misses hops or a dynamic boundary.
- "pass" only if it hits the must-hit symbols in roughly the right order AND does not fabricate.
- Penalize confident-but-wrong (fabrication / wrongClaims) HARDER than an honest "I could not determine X".
Reply with ONLY a JSON object, no prose, no code fences:
{"verdict":"pass|partial|fail","score":0.0,"missedHops":[],"wrongClaims":[],"fabrication":false,"rationale":""}`;

const res = spawnSync(
  "claude",
  ["-p", prompt, "--model", model, "--output-format", "json",
   "--mcp-config", "{}", "--strict-mcp-config"],
  { cwd: tmpdir(), encoding: "utf-8", maxBuffer: 1024 * 1024 * 16 }
);
if (res.status !== 0) {
  console.error("eval_judge: claude failed:", res.stderr || res.error);
  process.exit(2);
}

let resultText = "";
try {
  resultText = JSON.parse(res.stdout).result || "";
} catch {
  resultText = res.stdout;
}
// Strip code fences / extract the first JSON object.
const match = resultText.match(/\{[\s\S]*\}/);
let verdict;
try {
  verdict = JSON.parse(match ? match[0] : resultText);
} catch {
  verdict = { verdict: "fail", score: 0, missedHops: [], wrongClaims: [],
              fabrication: false, rationale: "judge output unparseable" };
}
process.stdout.write(JSON.stringify(verdict));
