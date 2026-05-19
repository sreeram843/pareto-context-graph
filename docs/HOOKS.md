# Hooks

`code-graph-mcp` supports repository-local hooks in `.code-graph/hooks/`.

## Hook files

Place Python files under `.code-graph/hooks/*.py` with one or both callables:

- `pre_context(payload: dict) -> dict`
- `post_context(response: dict) -> dict`

## Example

```python
def pre_context(payload: dict) -> dict:
    payload.setdefault("token_budget", 30000)
    return payload


def post_context(response: dict) -> dict:
    response["hook_marker"] = "applied"
    return response
```

## Safety redaction

By default, context responses redact common secret patterns (for example API keys).
Use `"no_safety": true` only for trusted local debugging.

## Git Hook: auto-update on commit

Add this to `.git/hooks/post-commit` (make it executable with
`chmod +x`) to keep the graph fresh automatically:

```bash
#!/bin/sh
# .git/hooks/post-commit — auto-update code-graph after each commit
code-graph-mcp update --repo "$(git rev-parse --show-toplevel)" &
```

This runs the incremental update in the background so it doesn't slow
down your commit workflow.
