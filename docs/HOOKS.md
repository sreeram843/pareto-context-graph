# Hooks

`pareto-context-graph` supports repository-local hooks in `.pareto-context-graph/hooks/`.

## Hook files

Place Python files under `.pareto-context-graph/hooks/*.py` with one or more callables:

| Callable | When it runs |
|----------|----------------|
| `pre_context(payload: dict) -> dict` | Before the context pipeline |
| `post_context(response: dict) -> dict` | After packing, before JSON is returned |
| `post_build(result: dict) -> dict` | After `build` completes |
| `post_update(result: dict) -> dict` | After `update` completes |

Hooks are loaded in sorted filename order. Each function receives and returns a dict.

## Minimal example

```python
def pre_context(payload: dict) -> dict:
    payload.setdefault("token_budget", 30000)
    return payload


def post_context(response: dict) -> dict:
    response["hook_marker"] = "applied"
    return response
```

## Feedback hook (recommended)

Shipped example: [`docs/examples/hooks/feedback_hints.py`](examples/hooks/feedback_hints.py)

Install in your repo:

```bash
mkdir -p .pareto-context-graph/hooks
cp docs/examples/hooks/feedback_hints.py .pareto-context-graph/hooks/
```

After every `context` call, the hook adds a `feedback_hints` block with the
response `request_id` and ready-to-send MCP commands (`feedback_accept`,
`mark_used`, `feedback_reject`, `feedback_dwell`). Agents (or IDE glue) can
forward those when the user actually uses or rejects files.

Example response fragment:

```json
{
  "request_id": "…",
  "context_files": […],
  "feedback_hints": {
    "request_id": "…",
    "paths_in_response": ["src/auth.py", "…"],
    "commands": {
      "accept_helpful": {
        "command": "feedback_accept",
        "request_id": "…",
        "paths": ["src/auth.py"]
      },
      "mark_used_in_edit": { "command": "mark_used", … }
    }
  }
}
```

Full feedback loop: [FEEDBACK.md](FEEDBACK.md) · nightly `pareto-context-graph learn`

When a returned path was rejected **≥3 times in the last 7 days**, `feedback_hints` also includes:

```json
"codify_suggestion": {
  "path": "src/noisy.py",
  "reject_count": 3,
  "reason": "rejected 3× in last 7 days",
  "hint": "Add a .cursor rule, docs snippet, or context-map entry for this area."
}
```

(Phase 15.7 — closes the loop from feedback to durable codified context.)

### Hook policy (optional)

When `.pareto-context-graph/policy.json` sets `allowed_hook_sha256`, only hooks whose
SHA-256 digest is listed are loaded. See `tests/test_phase7.py::test_hook_policy_allowlist`.

## Safety redaction

By default, context responses redact common secret patterns (for example API keys).
Use `"no_safety": true` only for trusted local debugging (requires policy when configured).

## Git Hook: auto-update on commit

Add this to `.git/hooks/post-commit` (make it executable with
`chmod +x`) to keep the graph fresh automatically:

```bash
#!/bin/sh
# .git/hooks/post-commit — auto-update pareto-context-graph after each commit
pareto-context-graph update --repo "$(git rev-parse --show-toplevel)" &
```

This runs the incremental update in the background so it doesn't slow
down your commit workflow.

## Pre/post-change hooks (Phase 15)

Examples for codified-context workflow:

- [`docs/examples/hooks/pre_change.py`](examples/hooks/pre_change.py) — tier-1 defaults before edits
- [`docs/examples/hooks/post_change.py`](examples/hooks/post_change.py) — surface `knowledge_gap` / `routing_hints`

Optional repo config (copy into `.pareto-context-graph/`):

- [`docs/examples/context-map.json`](examples/context-map.json) — subsystem → spec paths for `doctor` drift checks
- [`docs/examples/routing.json`](examples/routing.json) — intent/path → specialist hints on `context`

See [PHASES_CODIFIED_CONTEXT.md](PHASES_CODIFIED_CONTEXT.md).

### Spec search (Phase 15.5)

Indexed on every `build` / `update` (when markdown changes):

- `docs/`, `doc/`, `.cursor/rules/`, `.github/`
- Root: `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, …
- Paths listed in `context-map.json` → `specs`

```json
{
  "command": "context",
  "query": "OAuth2 bearer authentication",
  "files": ["fastapi/security/oauth2.py"],
  "include_specs": true,
  "spec_limit": 5
}
```

Response includes `spec_context.snippets` (path, kind, title, snippet, score). The `search` command also returns `spec_hits`.

### Subsystems (Phase 15.6)

```json
{ "command": "list_subsystems" }
```

```json
{ "command": "subsystem_files", "subsystem": "src/pareto_context_graph", "file_limit": 50 }
```

Manual subsystems come from `context-map.json`; auto clusters group by directory prefix (`src/pkg`, `tests`, …).
