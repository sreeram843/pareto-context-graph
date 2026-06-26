# Feedback Learning

The server logs returned context files to the `feedback` table.
Clients can mark files as actually used via MCP `mark_used` and related
`feedback_*` commands.

## Event log

Append-only `.pareto-context-graph/events.jsonl` records `context_request` counterfactuals
plus client events (`feedback_cite`, `feedback_accept`, `feedback_reject`,
`feedback_view`, `feedback_dwell`, `mark_used`).

Every `context` response includes a `request_id`. Pass it back on feedback
commands so labels attach to the correct candidate pool.

## Learn weights

Run:

```bash
pareto-context-graph learn
# optional: force logistic or LambdaMART (needs pip install -e '.[ranker]')
pareto-context-graph learn --ranker auto
```

This folds `events.jsonl` into SQLite, then writes:

- `.pareto-context-graph/weights.json` — per-file logit boosts
- `.pareto-context-graph/prune_weights.json` — per-file keep bias for learned tier-3 prune
- `.pareto-context-graph/ranker.json` — logistic or LambdaMART re-ranker (when enough labels)
- `.pareto-context-graph/ranker.lgb.txt` — LightGBM booster (LambdaMART only)

During `context` ranking, learned file weights and the ranker blend with the
prior score.

## Nightly learn (cron example)

```cron
# Fold feedback and refresh weights every night at 2am
0 2 * * * cd /path/to/repo && pareto-context-graph learn >> /var/log/pareto-context-graph-learn.log 2>&1
```

For MCP `serve --watch`, events are folded every 30s by the background flusher;
`learn` is still useful for ranker training and compaction.

## Held-out replay eval

Prove ranking improves from synthetic feedback:

```bash
pareto-context-graph eval --repo-map fastapi=bench/fastapi --feedback-replay
```

See [tests/eval/feedback_replay.md](../tests/eval/feedback_replay.md).

## MCP feedback commands

After `context`, use the response `request_id`:

```json
{"command": "feedback_accept", "request_id": "<id>", "paths": ["src/auth.py"]}
{"command": "feedback_cite", "request_id": "<id>", "paths": ["src/auth.py"]}
{"command": "feedback_reject", "request_id": "<id>", "paths": ["src/noise.py"]}
{"command": "feedback_view", "request_id": "<id>", "paths": ["src/glanced.py"]}
{"command": "feedback_dwell", "request_id": "<id>", "paths": ["src/auth.py"], "dwell_seconds": 45}
{"command": "mark_used", "request_id": "<id>", "paths": ["src/auth.py"]}
```

Signal strength (highest → lowest): `mark_used`, `feedback_accept`, `feedback_cite`,
`feedback_dwell` (≥30s), `feedback_view`, `feedback_reject`.

## Client integration (Cursor / agent loop)

### 1. Capture `request_id` from every `context` call

```json
{
  "context_files": [...],
  "request_id": "ctx-7f3a..."
}
```

Store `request_id` alongside the files you injected into the prompt.

### 2. Emit feedback when the user acts

| User action | MCP command |
|-------------|-------------|
| Accepts a suggested file | `feedback_accept` |
| Assistant cites a file in the answer | `feedback_cite` |
| File was useless | `feedback_reject` |
| Opened but ignored | `feedback_view` |
| Kept file open ≥30s | `feedback_dwell` with `dwell_seconds` |
| Actually used in the edit | `mark_used` |

### 3. Dwell tracking (`feedback_dwell`)

Track when a context file tab or diff view stays focused:

1. On `context`, record `{request_id, path, opened_at}` for each injected file.
2. On tab close, blur, or session end, compute `dwell_seconds = now - opened_at`.
3. If `dwell_seconds >= 30`, call `feedback_dwell` (counts as positive).
4. If the user opened briefly (<30s) without using, `feedback_view` is enough.

Example agent hook (pseudo-code):

```python
from pareto_context_graph.api import ParetoContextGraph

cg = ParetoContextGraph("/path/to/repo")
result = cg.context(files=["src/main.py"], query="add auth middleware")
request_id = result["request_id"]

# ... user works with context ...

cg.feedback_dwell(request_id, ["src/auth.py"], dwell_seconds=52.0)
cg.mark_used(["src/auth.py"], request_id=request_id)
```

### 4. Cursor MCP wiring

Copy the [feedback hints hook](HOOKS.md#feedback-hook-recommended) into
`.pareto-context-graph/hooks/` so every `context` response includes `feedback_hints`
with the correct `request_id`.

After `pareto-context-graph install --platform cursor`, add feedback calls in your
agent rules or forward the hook's `feedback_hints.commands`:

```text
When the user confirms a context file was helpful, call pareto_context_graph with
command feedback_accept and the request_id from the last context response.
When a suggested file stays open 30+ seconds, call feedback_dwell.
Always pass request_id from the context response.
```

The MCP server batches writes every 30s (`FeedbackFlusher`); no client-side
batching is required.

### 5. Python API (no MCP)

```python
from pareto_context_graph.api import ParetoContextGraph

cg = ParetoContextGraph("/path/to/repo")
result = cg.context(files=["app/models/user.rb"], query="password reset")
rid = result["request_id"]

cg.feedback_accept(rid, ["app/models/user.rb"])
cg.feedback_dwell(rid, ["app/services/mailer.rb"], dwell_seconds=40)
cg.mark_used(["app/models/user.rb"], request_id=rid)
cg.learn()  # fold events + train ranker
```

## Optional LambdaMART ranker

Install the optional extra for listwise learning-to-rank:

```bash
pip install -e ".[ranker]"
pareto-context-graph learn --ranker auto
```

When `lightgbm` is unavailable or labels are sparse, training falls back to the
stdlib logistic ranker automatically.
